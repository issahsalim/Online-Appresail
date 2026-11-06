from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Group

# ==========================================
# 1. ORGANIZATIONAL STRUCTURE
# ==========================================

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class EmployeeProfile(models.Model):
    ROLE_CHOICES = [
        ('Employee', 'Employee'),
        ('Supervisor', 'Supervisor'),
        ('HOD', 'Head of Department'),
        ('HR', 'HR Manager'),
    ]
    TITLE_CHOICES = [
        ('mr', 'Mr.'),
        ('mrs', 'Mrs.'),
        ('ms', 'Ms.'),
        ('other', 'Other'),
    ]
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    
    # Using the User model (Staff ID should be saved in user.username)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='Employee')
    title = models.CharField(max_length=10, choices=TITLE_CHOICES, blank=True)
    other_title = models.CharField(max_length=50, blank=True)
    other_names = models.CharField(max_length=150, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    appointment_to_present_grade_date = models.DateField(null=True, blank=True)

    def __str__(self):
        dept = self.department.name if self.department else 'No Dept'
        return f"{self.user.username} - {self.role} ({dept})"


class AppNotification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='app_notifications',
    )
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=300)
    url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def is_read(self):
        return self.read_at is not None

    def __str__(self):
        return f"{self.title} for {self.recipient}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('deleted', 'Deleted'),
        ('login', 'Logged in'),
        ('logout', 'Logged out'),
        ('password_changed', 'Password changed'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    actor_username = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=20, blank=True)
    actor_is_superuser = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    object_label = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['actor_is_superuser', 'created_at']),
            models.Index(fields=['model_name', 'object_id']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} {self.model_name} #{self.object_id}"


class DepartmentSupervisor(models.Model):
    department = models.OneToOneField(
        Department,
        on_delete=models.CASCADE,
        related_name='supervisor_assignment',
    )
    supervisor = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name='led_departments',
    )

    def clean(self):
        if self.supervisor_id and self.supervisor.role == 'HR':
            raise ValidationError({'supervisor': 'HR users cannot be assigned as department reviewers.'})
        if (
            self.department_id and self.supervisor_id
            and self.supervisor.department_id != self.department_id
        ):
            raise ValidationError({
                'supervisor': 'The department supervisor must belong to that department.',
            })

    def __str__(self):
        return f"{self.department} - {self.supervisor}"

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class SupervisorReporting(models.Model):
    supervisor = models.OneToOneField(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name='reporting_line',
        limit_choices_to={'role': 'Supervisor'},
    )
    manager = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name='direct_reports',
        limit_choices_to={'role': 'HOD'},
    )

    def clean(self):
        if self.supervisor_id and self.supervisor.role != 'Supervisor':
            raise ValidationError({'supervisor': 'Choose a user with the Supervisor role.'})
        if self.manager_id and self.manager.role != 'HOD':
            raise ValidationError({'manager': 'Choose a user with the HOD role.'})
        if self.supervisor_id and self.supervisor_id == self.manager_id:
            raise ValidationError({'manager': 'A supervisor cannot report to themselves.'})

        manager_id = self.manager_id
        visited_manager_ids = set()
        while manager_id:
            if manager_id == self.supervisor_id:
                raise ValidationError({'manager': 'This reporting line would create a cycle.'})
            if manager_id in visited_manager_ids:
                raise ValidationError({'manager': 'The existing reporting lines contain a cycle.'})
            visited_manager_ids.add(manager_id)
            next_line = SupervisorReporting.objects.filter(
                supervisor_id=manager_id,
            ).exclude(pk=self.pk).values_list('manager_id', flat=True).first()
            manager_id = next_line

    def __str__(self):
        return f"{self.supervisor} reports to {self.manager}"

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


def get_assigned_supervisor(profile):
    if profile.role == 'Supervisor':
        reporting_line = SupervisorReporting.objects.filter(
            supervisor=profile,
        ).select_related('manager__user').first()
        return reporting_line.manager if reporting_line else None
    if profile.role == 'Employee' and profile.department_id:
        assignment = DepartmentSupervisor.objects.filter(
            department_id=profile.department_id,
        ).select_related('supervisor__user').first()
        if not assignment:
            return None
        if assignment.supervisor_id == profile.id:
            hod_assignment = AssignDepartmentHod.objects.filter(
                department_id=profile.department_id,
            ).select_related('hod__user').first()
            return hod_assignment.hod if hod_assignment else None
        return assignment.supervisor
    return None


class AssignDepartmentHod(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='department_hod')
    
    hod = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='hod_department', 
        limit_choices_to={'role': 'HOD'}
    ) 


# ==========================================
# 2. APPRAISAL CYCLE & MASTER DATA
# ==========================================

class AppraisalCycle(models.Model):
    year = models.IntegerField(unique=True)
    is_active = models.BooleanField(default=True)
    report_period_from = models.DateField(null=True, blank=True)
    report_period_to = models.DateField(null=True, blank=True)
        
    def __str__(self):
        return f"Cycle {self.year}"

    def clean(self):
        if (
            self.report_period_from
            and self.report_period_to
            and self.report_period_to < self.report_period_from
        ):
            raise ValidationError({
                'report_period_to': 'The report period end date must be on or after its start date.',
            })

    class Meta:
        ordering = ['-year']


class Competency(models.Model):
    TYPE_CHOICES = [('core', 'Core'), ('non_core', 'Non-Core')]
    
    name = models.CharField(max_length=255)
    competency_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    description = models.TextField(
        blank=True,
        help_text='Enter each description item on a separate line.',
    )

    class Meta:
        ordering = ['competency_type', 'display_order', 'name']

    def __str__(self):
        return f"[{self.get_competency_type_display()}] {self.name}"

    @property
    def description_items(self):
        return [
            item.strip().lstrip('-* ').strip()
            for item in self.description.splitlines()
            if item.strip()
        ]


# ==========================================
# 3. THE APPRAISAL FORM (HEADER)
# ==========================================

class Appraisal(models.Model):
    ASSESSMENT_DECISION_CHOICES = [
        ('promote_now', 'Outstanding - should be promoted as soon as possible'),
        ('suitable', 'Suitable for promotion'),
        ('ready_2_3_years', 'Likely to be ready for promotion in 2 to 3 years'),
        ('not_ready', 'Not ready for promotion for at least 3 years / unlikely to be promoted further'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft (Employee filling)'),
        ('pending_manager', 'Pending Supervisor Review'),
        ('pending_hod', 'Pending HOD Review'),
        ('pending_hr', 'Pending HR Final Approval'),
        ('returned_employee', 'Returned to Employee'),
        ('returned_supervisor', 'Returned to Supervisor'),
        ('returned_hod', 'Returned to HOD'),
        ('completed', 'Completed & Locked'),
    ]

    cycle = models.ForeignKey(AppraisalCycle, on_delete=models.CASCADE, related_name="appraisals")
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name="my_appraisals")
    employee_title = models.CharField(max_length=10, choices=EmployeeProfile.TITLE_CHOICES, blank=True)
    employee_other_title = models.CharField(max_length=50, blank=True)
    employee_first_name = models.CharField(max_length=150, blank=True)
    employee_last_name = models.CharField(max_length=150, blank=True)
    employee_other_names = models.CharField(max_length=150, blank=True)
    employee_gender = models.CharField(max_length=10, choices=EmployeeProfile.GENDER_CHOICES, blank=True)
    employee_job_title = models.CharField(max_length=150, blank=True)
    employee_department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appraisals',
    ) 
    employee_department_name = models.CharField(max_length=100, blank=True)
    employee_appointment_to_present_grade_date = models.DateField(null=True, blank=True)
    
    # People in the flow
    supervisor = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="supervisor_appraisals")
    hod = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="hod_appraisals")
    
    # Workflow Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Timestamps (Audit Trail)
    employee_signed_at = models.DateTimeField(null=True, blank=True)
    supervisor_signed_at = models.DateTimeField(null=True, blank=True)
    hod_signed_at = models.DateTimeField(null=True, blank=True)
    hr_approved_at = models.DateTimeField(null=True, blank=True)

    # Final Calculated Score (Filled by System at End of Year)
    overall_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    final_rating = models.CharField(max_length=50, blank=True) 

    # Comments for each stage (Rules enforced in views/templates)
    employee_comments = models.TextField(blank=True, null=True)
    supervisor_comments = models.TextField(blank=True, null=True)
    career_development_plan = models.TextField(blank=True)
    assessment_decision = models.CharField(
        max_length=20,
        choices=ASSESSMENT_DECISION_CHOICES,
        blank=True,
    )
    hod_comments = models.TextField(blank=True, null=True)
    hr_comments = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.employee.user.username} - {self.cycle.year} ({self.get_status_display()})"

    def calculate_final_score(self):
        """Calculate M + N + O and convert the total to a percentage."""
        self.validate_final_score_inputs()
        breakdown = self.get_score_breakdown()
        total = breakdown['total']
        self.overall_percentage = (
            total / Decimal('5') * Decimal('100')
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if self.overall_percentage >= 80:
            self.final_rating = "Outstanding"
        elif self.overall_percentage >= 65:
            self.final_rating = "Very Good"
        elif self.overall_percentage >= 50:
            self.final_rating = "Satisfactory"
        elif self.overall_percentage >= 41:
            self.final_rating = "Needs Improvement"
        else:
            self.final_rating = "Unsatisfactory"

    def get_score_breakdown(self):
        """Return the target and selected-competency calculation for display."""
        target_rows = list(self.targets.all())
        target_scores = [target.end_year_score for target in target_rows]
        targets_complete = bool(target_rows) and all(
            score is not None for score in target_scores
        ) 
        target_total = (
            sum(Decimal(score) for score in target_scores)
            if targets_complete else None
        )
        target_average = (
            target_total / len(target_rows) if targets_complete else None
        ) 
        target_component = (
            target_average * Decimal('0.6')
            if target_average is not None else None
        )

        groups = []
        for competency_score in self.competency_scores.select_related(
            'competency',
        ).prefetch_related('statement_scores').order_by(
            'competency__competency_type',
            'competency__display_order',
            'competency__name',
        ):
            weight = (
                Decimal('0.3')
                if competency_score.competency.competency_type == 'core'
                else Decimal('0.1')
            )
            statements = list(competency_score.statement_scores.all())
            if not statements and competency_score.end_year_score is not None:
                statement_rows = [{
                    'statement': 'Previously recorded competency score',
                    'score': competency_score.end_year_score,
                    'weighted_score': weight * Decimal(competency_score.end_year_score),
                }]
            else:
                statement_rows = [
                    {
                        'statement': statement.statement,
                        'score': statement.end_year_score,
                        'weighted_score': (
                            weight * Decimal(statement.end_year_score)
                            if statement.end_year_score is not None else None
                        ),
                    }
                    for statement in statements
                ]

            group_complete = bool(statement_rows) and all(
                row['score'] is not None for row in statement_rows
            )
            group_total = (
                sum(row['weighted_score'] for row in statement_rows)
                if group_complete else None
            )
            group_average = (
                group_total / len(statement_rows) if group_complete else None
            )
            groups.append({
                'score': competency_score,
                'competency': competency_score.competency,
                'name': competency_score.competency.name,
                'type': competency_score.competency.competency_type,
                'type_display': competency_score.competency.get_competency_type_display(),
                'weight': weight,
                'statements': statement_rows,
                'legacy': not statements and competency_score.end_year_score is not None,
                'complete': group_complete,
                'total': group_total,
                'average': group_average,
            })

        core_groups = [group for group in groups if group['type'] == 'core']
        non_core_groups = [group for group in groups if group['type'] == 'non_core']

        def category_average(category_groups):
            if not category_groups:
                return Decimal('0')
            if any(not group['complete'] for group in category_groups):
                return None
            return sum(group['average'] for group in category_groups) / len(category_groups)

        core_component = category_average(core_groups)
        non_core_component = category_average(non_core_groups)
        complete = (
            targets_complete
            and bool(groups)
            and all(group['complete'] for group in groups)
        )
        total = (
            target_component + core_component + non_core_component
            if complete else None
        )
        percentage = (
            (total / Decimal('5') * Decimal('100')).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP,
            )
            if total is not None else None
        )

        return {
            'targets': target_rows,
            'targets_complete': targets_complete,
            'target_total': target_total,
            'target_average': target_average,
            'target_component': target_component,
            'groups': groups,
            'core_component': core_component,
            'non_core_component': non_core_component,
            'total': total,
            'percentage': percentage,
            'complete': complete,
        }

    def validate_final_score_inputs(self):
        targets = list(self.targets.values_list('end_year_score', flat=True))
        if not targets:
            raise ValidationError('At least one target is required before scoring.')
        if any(score is None for score in targets):
            raise ValidationError('Score every target before submitting this appraisal.')

        competency_scores = self.competency_scores.prefetch_related('statement_scores')
        if not competency_scores.exists():
            raise ValidationError('At least one competency statement must be selected.')
        for competency_score in competency_scores:
            statements = list(competency_score.statement_scores.all())
            if not statements:
                raise ValidationError(
                    f'Select at least one statement for {competency_score.competency.name}.'
                )
            if any(statement.end_year_score is None for statement in statements):
                raise ValidationError(
                    f'Score every selected statement for {competency_score.competency.name}.'
                )


class AppraisalReturn(models.Model):
    RETURN_STAGE_CHOICES = [
        ('supervisor', 'Supervisor review'),
        ('hod', 'HOD review'),
        ('hr', 'HR review'),
    ]

    appraisal = models.ForeignKey(
        Appraisal,
        on_delete=models.CASCADE,
        related_name='returns',
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appraisal_returns',
    )
    reviewer_name = models.CharField(max_length=150)
    stage = models.CharField(max_length=20, choices=RETURN_STAGE_CHOICES)
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f"{self.appraisal} returned by {self.reviewer_name} ({self.stage})"


class AppraisalTraining(models.Model):
    appraisal = models.ForeignKey(
        Appraisal,
        on_delete=models.CASCADE,
        related_name='trainings',
    )
    institution = models.CharField(max_length=200, blank=True)
    date = models.DateField(null=True, blank=True)
    programme = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.appraisal} — {self.programme or self.institution or 'Training'}"


# ==========================================
# 4. APPRAISAL TARGETS (SECTION 2, 3 & 4)
# ==========================================

class AppraisalTarget(models.Model):
    MID_YEAR_PROGRESS_CHOICES = [
        ('achieved', 'Target achieved'),
        ('partial', 'Target partially achieved'),
        ('not_achieved', 'Target not achieved'),
    ]
    MID_YEAR_REMARK_CHOICES = [
        ('maintain', 'Maintain target'),
        ('re_evaluate', 'Re-evaluate target'),
        ('change', 'Change target'),
    ]
    END_YEAR_ASSESSMENT_CHOICES = [
        ('completed', 'Target completed'),
        ('uncompleted', 'Target uncompleted'),
    ]
    END_YEAR_COMMENT_CHOICES = [
        ('very_good', 'Very Good'),
        ('satisfactory', 'Satisfactory'),
        ('need_improvement', 'Need Improvement'),
        ('unsatisfactory', 'Unsatisfactory'),
    ]

    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE, related_name="targets")
    
    # Section 2: Employee fills this in January
    key_result_area = models.CharField(max_length=255)
    target_description = models.TextField()
    resources_required = models.TextField(blank=True)

    # Section 3: Supervisor fills this in July
    mid_year_progress = models.CharField(max_length=20, choices=MID_YEAR_PROGRESS_CHOICES, null=True, blank=True)
    mid_year_remark = models.CharField(max_length=20, choices=MID_YEAR_REMARK_CHOICES, null=True, blank=True)

    # Section 4: Supervisor fills this in December
    end_year_assessment = models.CharField(max_length=20, choices=END_YEAR_ASSESSMENT_CHOICES, null=True, blank=True)
    end_year_weight = models.IntegerField(default=5)
    end_year_score = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    end_year_comment = models.CharField(max_length=20, choices=END_YEAR_COMMENT_CHOICES, null=True, blank=True)

    def __str__(self):
        return f"Target: {self.key_result_area}"


# ==========================================
# 5. APPRAISAL COMPETENCIES (SECTION 3 & 5)
# ==========================================

class AppraisalCompetencyScore(models.Model):
    MID_YEAR_PROGRESS_CHOICES = [
        ('fairly_exhibited', 'Fairly exhibited'),
        ('poorly_exhibited', 'Poorly exhibited'),
        ('well_exhibited', 'Well exhibited'),
        ('excellently_exhibited', 'Excellently exhibited'),
        ('exceptionally_exhibited', 'Exceptionally exhibited'),
    ]
    MID_YEAR_REMARK_CHOICES = [
        ('maintain_competency', 'Maintain competency'),
        ('change_competency', 'Change competency'),
    ]

    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE, related_name="competency_scores")
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT)

    # Section 3: Supervisor fills in July
    mid_year_progress = models.CharField(max_length=30, choices=MID_YEAR_PROGRESS_CHOICES, null=True, blank=True)
    mid_year_remark = models.CharField(max_length=30, choices=MID_YEAR_REMARK_CHOICES, null=True, blank=True)

    # Section 5: Supervisor fills in December (1 to 5 Radio Buttons)
    end_year_score = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['appraisal', 'competency'], name='unique_appraisal_competency')
        ]


class AppraisalCompetencyStatementScore(models.Model):
    competency_score = models.ForeignKey(
        AppraisalCompetencyScore,
        on_delete=models.CASCADE,
        related_name='statement_scores',
    )
    statement_index = models.PositiveIntegerField()
    statement = models.TextField()
    end_year_score = models.IntegerField(
        null=True, 
        blank=True,  
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )

    class Meta:
        ordering = ['statement_index']
        constraints = [
            models.UniqueConstraint(
                fields=['competency_score', 'statement_index'],
                name='unique_competency_statement_score',
            ),
        ]

    def __str__(self):
        return f"{self.competency_score.competency.name}: {self.statement}"


# ==========================================
# 6. AUTOMATION (SIGNALS)
# ==========================================

@receiver(post_save, sender=EmployeeProfile)
def manage_user_groups(sender, instance, **kwargs):
    """
    Automatically puts the user in the correct Django Group based on their role.
    """
    roles = ['Employee', 'Supervisor', 'HOD', 'HR']
    
    # Ensure all groups exist
    for role in roles:
        Group.objects.get_or_create(name=role)
        
    # Clear old groups and assign the new one
    instance.user.groups.clear()
    group = Group.objects.get(name=instance.role)
    instance.user.groups.add(group)