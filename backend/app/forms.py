# yourapp/forms.py
from django import forms
from django.contrib.auth import get_user_model
from .models import (
    Appraisal,
    AppraisalTarget,
    AppraisalCompetencyScore,
    Competency,
    Department,
    EmployeeProfile,
)


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name', 'email']


class HRSupervisorAssignmentForm(forms.Form):
    department = forms.ModelChoiceField(queryset=Department.objects.order_by('name'))
    employee = forms.ModelChoiceField(queryset=EmployeeProfile.objects.none())
    supervisor = forms.ModelChoiceField(queryset=EmployeeProfile.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        department_id = self.data.get('department') or self.initial.get('department')
        if department_id:
            self.fields['employee'].queryset = EmployeeProfile.objects.filter(
                role='Employee',
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

# We use Django's FormSet to allow the employee to add multiple targets at once
TargetFormSet = forms.inlineformset_factory(
    Appraisal, 
    AppraisalTarget, 
    form=TargetForm, 
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['competencies'].queryset = Competency.objects.order_by(
            'competency_type',
            'name',
        )


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

class SupervisorCompetencyForm(forms.ModelForm):
    class Meta:
        model = AppraisalCompetencyScore
        # Supervisor scores the competencies
        fields = ['mid_year_progress', 'mid_year_remark', 'end_year_score']
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