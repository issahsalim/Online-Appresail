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


class AssignSupervisor(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='department_supervisor')
    
    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='employee_supervisor', 
        limit_choices_to={'role__in': ['Employee', 'Supervisor']}
    )
    supervisor = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='supervisor_employee', 
        limit_choices_to={'role': 'Supervisor'}
    ) 

    class Meta:
        unique_together = ('employee', 'supervisor') 


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


class Competency(models.Model):
    TYPE_CHOICES = [('core', 'Core'), ('non_core', 'Non-Core')]
    
    name = models.CharField(max_length=255)
    competency_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    description = models.TextField(
        blank=True,
        help_text='Enter each description item on a separate line.',
    )

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
    hod_comments = models.TextField(blank=True, null=True)
    hr_comments = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.employee.user.username} - {self.cycle.year} ({self.get_status_display()})"

    def calculate_final_score(self):
        """Calculates the overall percentage and final rating."""
        from django.db.models import Avg
        
        # 1. Calculate Average Target Score (Scale of 1-5)
        target_avg = self.targets.aggregate(avg=Avg('end_year_score'))['avg'] or 0
        
        # 2. Calculate Average Competency Score (Scale of 1-5)
        comp_avg = self.competency_scores.aggregate(avg=Avg('end_year_score'))['avg'] or 0
        
        # 3. Overall Average (Assuming 50% weight for targets, 50% for competencies for simplicity)
        # You can adjust these weights based on the exact PDF formula.
        overall_avg = (target_avg + comp_avg) / 2
        
        # 4. Convert to Percentage (Average 5 = 100%, Average 1 = 20%)
        self.overall_percentage = (overall_avg / 5) * 100
        
        # 5. Assign Final Rating based on the PDF Scale
        if self.overall_percentage >= 80:
            self.final_rating = "Outstanding"
        elif self.overall_percentage >= 65:
            self.final_rating = "Very Good"
        elif self.overall_percentage >= 50:
            self.final_rating = "Satisfactory"
        elif self.overall_percentage >= 41:
            self.final_rating = "Needs Improvement"
        else:
            self.final_rating = "Unacceptable"


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
        ('demonstrated', 'Competency demonstrated'),
        ('not_demonstrated', 'Competency not demonstrated'),
    ]
    MID_YEAR_REMARK_CHOICES = [
        ('exceptional', 'Exceptional'),
        ('exceeds', 'Exceeds expectations'),
        ('meets', 'Meets expectations'),
        ('below', 'Below expectation'),
        ('unacceptable', 'Unacceptable'),
    ]

    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE, related_name="competency_scores")
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT)

    # Section 3: Supervisor fills in July
    mid_year_progress = models.CharField(max_length=20, choices=MID_YEAR_PROGRESS_CHOICES, null=True, blank=True)
    mid_year_remark = models.CharField(max_length=20, choices=MID_YEAR_REMARK_CHOICES, null=True, blank=True)

    # Section 5: Supervisor fills in December (1 to 5 Radio Buttons)
    end_year_score = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['appraisal', 'competency'], name='unique_appraisal_competency')
        ]


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
