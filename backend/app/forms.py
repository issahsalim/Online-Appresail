# yourapp/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from .models import (
    Appraisal,
    AppraisalTraining,
    AppraisalTarget,
    AppraisalCompetencyScore,
    AppraisalCompetencyStatementScore,
    Competency,
    Department,
    EmployeeProfile,
    AppraisalCycle
)

class AppraisalCycleForm(forms.ModelForm):
    class Meta:
        model = AppraisalCycle
        fields = ['year', 'report_period_from', 'report_period_to', 'is_active']
        widgets = {
            'year': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 2026',
                'inputmode': 'numeric',
            }),
            'report_period_from': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'report_period_to': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
        labels = {
            'year': 'Appraisal cycle year',
            'report_period_from': 'Report period starts',
            'report_period_to': 'Report period ends',
            'is_active': 'Make this cycle active',
        }
        help_texts = {
            'year': 'Use a unique year for this appraisal cycle.',
            'report_period_from': 'Optional start date shown on appraisal forms.',
            'report_period_to': 'Optional end date shown on appraisal forms.',
            'is_active': 'Employees and reviewers can work on active cycles.',
        }



class UserProfileForm(forms.ModelForm):
    title = forms.ChoiceField(
        choices=[('', 'Select title')] + EmployeeProfile.TITLE_CHOICES,
        required=False,
    )
    other_title = forms.CharField(required=False, max_length=50)
    other_names = forms.CharField(required=False, max_length=150)
    gender = forms.ChoiceField(
        choices=[('', 'Select gender')] + EmployeeProfile.GENDER_CHOICES,
        required=False,
    )
    job_title = forms.CharField(required=False, max_length=150)
    appointment_to_present_grade_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name', 'email']
        labels = {
            'first_name': 'First name',
            'last_name': 'Surname',
            'email': 'Email address',
        }

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.fields['title'].label = 'Title'
        self.fields['other_title'].label = 'Other title (specify)'
        self.fields['other_names'].label = 'Other name(s)'
        self.fields['gender'].label = 'Gender'
        self.fields['job_title'].label = 'Present job title/position'
        self.fields['appointment_to_present_grade_date'].label = (
            'Date of appointment to present grade'
        )
        if profile:
            for field in (
                'title',
                'other_title',
                'other_names',
                'gender',
                'job_title',
                'appointment_to_present_grade_date',
            ):
                self.fields[field].initial = getattr(profile, field)

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit and self.profile:
            for field in (
                'title',
                'other_title',
                'other_names',
                'gender',
                'job_title',
                'appointment_to_present_grade_date',
            ):
                setattr(self.profile, field, self.cleaned_data[field])
            self.profile.save(update_fields=[
                'title',
                'other_title',
                'other_names',
                'gender',
                'job_title',
                'appointment_to_present_grade_date',
            ])
        return user


class AppraisalEmployeeInfoForm(forms.ModelForm):
    class Meta:
        model = Appraisal
        fields = [
            'employee_title',
            'employee_other_title',
            'employee_first_name',
            'employee_last_name',
            'employee_other_names',
            'employee_gender',
            'employee_job_title',
            'employee_department',
            'employee_appointment_to_present_grade_date',
            'employee_comments',
        ]
        labels = {
            'employee_title': 'Title',
            'employee_other_title': 'Other title (specify)',
            'employee_first_name': 'First name',
            'employee_last_name': 'Surname',
            'employee_other_names': 'Other name(s)',
            'employee_gender': 'Gender',
            'employee_job_title': 'Present job title/position',
            'employee_department': 'Department / unit',
            'employee_appointment_to_present_grade_date':
                'Date of appointment to present grade',
            'employee_comments': "Appraisee's comments on overall assessment",
        }
        widgets = {
            'employee_appointment_to_present_grade_date': forms.DateInput(
                attrs={'type': 'date'},
            ),
            'employee_comments': forms.Textarea(attrs={'rows': 5}),
        }

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee_title'].required = False
        self.fields['employee_gender'].required = False
        self.fields['employee_other_title'].required = False
        self.fields['employee_first_name'].required = False
        self.fields['employee_last_name'].required = False
        self.fields['employee_other_names'].required = False
        self.fields['employee_job_title'].required = False
        self.fields['employee_department'].required = False
        self.fields['employee_appointment_to_present_grade_date'].required = False
        self.fields['employee_department'].queryset = Department.objects.order_by('name')
        profile_values = {}
        if profile:
            profile_values = {
                'employee_title': profile.title,
                'employee_other_title': profile.other_title,
                'employee_first_name': profile.user.first_name,
                'employee_last_name': profile.user.last_name,
                'employee_other_names': profile.other_names,
                'employee_gender': profile.gender,
                'employee_job_title': profile.job_title,
                'employee_department': profile.department,
                'employee_appointment_to_present_grade_date':
                    profile.appointment_to_present_grade_date,
            }

        for field_name in self.fields:
            field = self.fields[field_name]
            instance_value = getattr(self.instance, field_name)
            if field_name == 'employee_comments':
                if instance_value:
                    self.initial[field_name] = instance_value
                continue
            if field_name == 'employee_department':
                has_appraisal_value = bool(
                    self.instance.employee_department_id
                    or self.instance.employee_department_name
                )
                profile_value = profile_values.get(field_name)
                initial_value = (
                    self.instance.employee_department
                    if self.instance.employee_department_id
                    else profile_value if not has_appraisal_value else None
                )
            else:
                has_appraisal_value = bool(instance_value)
                profile_value = profile_values.get(field_name)
                initial_value = instance_value or profile_value

            if initial_value not in (None, ''):
                self.initial[field_name] = initial_value
            if has_appraisal_value or profile_value not in (None, ''):
                field.disabled = True

    def save(self, commit=True):
        appraisal = super().save(commit=commit)
        if commit and appraisal.employee_department_id:
            appraisal.employee_department_name = appraisal.employee_department.name
            appraisal.save(update_fields=['employee_department_name'])
        return appraisal


class AppraisalTrainingForm(forms.ModelForm):
    class Meta:
        model = AppraisalTraining
        fields = ['institution', 'date', 'programme']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


AppraisalTrainingFormSet = forms.inlineformset_factory(
    Appraisal,
    AppraisalTraining,
    form=AppraisalTrainingForm,
    extra=4,
    max_num=4,
    can_delete=False,
)


class HRSupervisorAssignmentForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.order_by('name'))
    employee = forms.ModelChoiceField(
        queryset=EmployeeProfile.objects.none(),
        error_messages={'invalid_choice': "Choose an employee from the selected department."},
    )
    supervisor = forms.ModelChoiceField(
        queryset=EmployeeProfile.objects.none(),
        error_messages={'invalid_choice': "Choose a supervisor from the selected department."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        department_id = self.data.get('department') or self.initial.get('department')
        if department_id:
            self.fields['employee'].queryset = EmployeeProfile.objects.filter(
                role__in=['Employee', 'Supervisor'],
                department_id=department_id,
            ).select_related('user').order_by('user__username')
            self.fields['supervisor'].queryset = EmployeeProfile.objects.filter(
                role='Supervisor',
                department_id=department_id,
            ).select_related('user').order_by('user__username')

    def clean(self):
        cleaned_data = super().clean()
        department = cleaned_data.get('department')
        employee = cleaned_data.get('employee')
        supervisor = cleaned_data.get('supervisor')

        if department and employee and employee.department_id != department.id:
            self.add_error('employee', "Choose an employee from the selected department.")
        if department and supervisor and supervisor.department_id != department.id:
            self.add_error('supervisor', "Choose a supervisor from the selected department.")
        if employee and supervisor and employee == supervisor:
            self.add_error('supervisor', "A supervisor cannot be assigned to themselves.")

        return cleaned_data


# ==========================================
# 1. EMPLOYEE FORM (Section 1 & 2)
# ==========================================
class TargetForm(forms.ModelForm):
    class Meta:
        model = AppraisalTarget
        # Only the employee's fields
        fields = ['key_result_area', 'target_description', 'resources_required']
        widgets = {
            'target_description': forms.Textarea(attrs={'rows': 3}),
            'resources_required': forms.Textarea(attrs={'rows': 2}),
        }

class EmployeeTargetFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if self.data.get('action') != 'submit' or any(self.errors):
            return

        if not any(
            form.cleaned_data.get('key_result_area')
            and form.cleaned_data.get('target_description')
            for form in self.forms
        ):
            raise ValidationError(
                'Add at least one key result area and its target before submitting.'
            )


# We use Django's FormSet to allow the employee to add multiple targets at once
TargetFormSet = forms.inlineformset_factory(
    Appraisal, 
    AppraisalTarget, 
    form=TargetForm, 
    formset=EmployeeTargetFormSet,
    extra=5, 
    max_num=5, 
    can_delete=False
)


class EmployeeCompetencySelectionForm(forms.Form):
    competencies = forms.ModelMultipleChoiceField(
        queryset=Competency.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the competencies that apply to your role.",
    )
    competency_statements = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        competencies = Competency.objects.order_by(
            'competency_type',
            'name',
        )
        self.fields['competencies'].queryset = competencies
        self.competency_groups = [
            {
                'competency': competency,
                'statements': [
                    {
                        'value': f'{competency.id}:{index}',
                        'text': statement,
                    }
                    for index, statement in enumerate(competency.description_items)
                ],
            }
            for competency in competencies
        ]
        self.fields['competency_statements'].choices = [
            (statement['value'], f"{group['competency'].name}: {statement['text']}")
            for group in self.competency_groups
            for statement in group['statements']
        ]


# ==========================================
# 2. SUPERVISOR FORM (Mid-Year & End-Year Scoring)
# ==========================================
class SupervisorTargetForm(forms.ModelForm):
    class Meta:
        model = AppraisalTarget
        # Supervisor can ONLY touch the mid-year and end-year fields
        fields = ['mid_year_progress', 'mid_year_remark', 'end_year_assessment', 'end_year_weight', 'end_year_score', 'end_year_comment']
        widgets = {
            'end_year_score': forms.NumberInput(attrs={'min': 1, 'max': 5}),
        }


class AppraiserReviewForm(forms.ModelForm):
    class Meta:
        model = Appraisal
        fields = [
            'supervisor_comments',
            'career_development_plan',
            'assessment_decision',
        ]
        labels = {
            'supervisor_comments': "Appraiser's comments on work plan achievements",
            'career_development_plan': 'Training and development - comments and plan',
            'assessment_decision': "Assessment of appraisee's potential",
        }
        widgets = {
            'supervisor_comments': forms.Textarea(attrs={'rows': 8}),
            'career_development_plan': forms.Textarea(attrs={'rows': 6}),
            'assessment_decision': forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supervisor_comments'].required = False
        self.fields['career_development_plan'].required = False
        self.fields['assessment_decision'].required = False


class SupervisorCompetencyForm(forms.ModelForm):
    class Meta:
        model = AppraisalCompetencyScore
        # Supervisor scores the competencies
        fields = ['mid_year_progress', 'mid_year_remark']


class SupervisorCompetencyStatementForm(forms.ModelForm):
    class Meta:
        model = AppraisalCompetencyStatementScore
        fields = ['end_year_score']
        widgets = {
            'end_year_score': forms.RadioSelect(choices=[(i, str(i)) for i in range(1, 6)]),
        }
        widgets = {
            # Render end_year_score as radio buttons (1 to 5) instead of a dropdown
            'end_year_score': forms.RadioSelect(choices=[(i, str(i)) for i in range(1, 6)]),
        }


# ==========================================
# 3. HOD & HR COMMENT FORMS
# ==========================================
class HODCommentForm(forms.ModelForm):
    class Meta:
        model = Appraisal
        fields = ['hod_comments']
        widgets = {
            'hod_comments': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Enter your HOD comments here...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['hod_comments'].required = True

class HRCommentForm(forms.ModelForm):
    class Meta:
        model = Appraisal
        fields = ['hr_comments']
        widgets = {
            'hr_comments': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Enter final HR comments here...'}),
        }


class AppraisalReturnForm(forms.Form):
    comment = forms.CharField(
        label="Reason for returning this appraisal",
        min_length=5,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Explain what must be corrected before this appraisal can continue.',
        }),
    )


# Add this to the bottom of forms.py
SupervisorTargetFormSet = forms.inlineformset_factory(
    Appraisal, 
    AppraisalTarget, 
    form=SupervisorTargetForm, 
    extra=0, # 0 means don't show empty rows; only show the targets the employee created
    can_delete=False
)

SupervisorCompetencyFormSet = forms.inlineformset_factory(
    Appraisal, 
    AppraisalCompetencyScore, 
    form=SupervisorCompetencyForm, 
    extra=0, 
    can_delete=False
)

SupervisorCompetencyStatementFormSet = forms.modelformset_factory(
    AppraisalCompetencyStatementScore,
    form=SupervisorCompetencyStatementForm,
    extra=0,
)