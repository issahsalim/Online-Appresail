from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.contrib import messages

from .models import (
    Appraisal,
    AppraisalCycle,
    AppraisalReturn,
    AssignSupervisor,
    Department,
    EmployeeProfile,
)
from .forms import (
    AppraisalReturnForm,
    EmployeeCompetencySelectionForm,
    HODCommentForm,
    HRCommentForm,
    HRSupervisorAssignmentForm,
    SupervisorCompetencyFormSet,
    SupervisorTargetFormSet,
    TargetFormSet,
    UserProfileForm,
)


@login_required
def dashboard_router(request):
    """Checks the user's role and redirects them to the correct dashboard."""
    profile = request.user.profile
    
    if profile.role == 'HR':
        return redirect('hr_dashboard')
    elif profile.role == 'HOD':
        return redirect('hod_dashboard')
    elif profile.role == 'Supervisor':
        return redirect('supervisor_dashboard')
    else:
        return redirect('employee_dashboard')

def employee_dashboard(request):
    """Shows the employee their current appraisal status."""
    # Get the active appraisal cycle
    cycle = AppraisalCycle.objects.filter(is_active=True).first()
    
    if not cycle:
        messages.error(request, "No active appraisal cycle right now.")
        return render(request, 'employee/dashboard.html', {'appraisal': None})

    # Find or create an appraisal for this employee for this year
    profile = request.user.profile
    appraisal, created = Appraisal.objects.get_or_create(
        employee=profile,
        cycle=cycle
    )

    return render(request, 'employee/dashboard.html', {
        'appraisal': appraisal,
        'latest_return': appraisal.returns.first(),
    })



def fill_targets(request):
    """Lets employees edit draft goals and view their appraisal after submission."""
    profile = request.user.profile
    cycle = AppraisalCycle.objects.filter(is_active=True).first()

    if not cycle:
        messages.error(request, "No active appraisal cycle right now.")
        return redirect('employee_dashboard')

    appraisal = get_object_or_404(Appraisal, employee=profile, cycle=cycle)

    if request.method == 'POST':
        if appraisal.status not in ['draft', 'returned_employee']:
            messages.warning(request, "Your submitted appraisal is locked and cannot be edited.")
            return redirect('fill_targets')

        formset = TargetFormSet(request.POST, instance=appraisal)
        competency_form = EmployeeCompetencySelectionForm(request.POST)
        if formset.is_valid() and competency_form.is_valid():
            selected_competencies = competency_form.cleaned_data['competencies']
            submit_for_review = request.POST.get('action') == 'submit'
            assignment = None

            if submit_for_review and not selected_competencies:
                messages.error(request, "Select at least one competency before submitting your appraisal.")
            elif submit_for_review:
                assignment = AssignSupervisor.objects.filter(
                    employee=profile,
                ).select_related('supervisor').first()
                if not assignment:
                    messages.error(
                        request,
                        "You cannot submit because no supervisor has been assigned to you yet. Contact HR.",
                    )

            if not submit_for_review or (selected_competencies and assignment):
                with transaction.atomic():
                    formset.save()
                    selected_ids = list(selected_competencies.values_list('id', flat=True))
                    appraisal.competency_scores.exclude(
                        competency_id__in=selected_ids,
                    ).delete()
                    for competency in selected_competencies:
                        appraisal.competency_scores.get_or_create(competency=competency)

                    if assignment:
                        appraisal.supervisor = assignment.supervisor
                        appraisal.status = 'pending_manager'
                        appraisal.employee_signed_at = timezone.now()
                        appraisal.save(update_fields=[
                            'supervisor',
                            'status',
                            'employee_signed_at',
                        ])

                if assignment:
                    messages.success(request, "Appraisal saved and submitted to your supervisor.")
                else:
                    messages.success(request, "Targets and competency selections saved successfully.")
                return redirect('employee_dashboard')
    else:
        formset = TargetFormSet(instance=appraisal)
        competency_form = EmployeeCompetencySelectionForm(
            initial={
                'competencies': appraisal.competency_scores.values_list(
                    'competency_id',
                    flat=True,
                ),
            }
        )

    if appraisal.status in ['draft', 'returned_employee']:
        if not formset.is_bound:
            formset = TargetFormSet(instance=appraisal)
        if not competency_form.is_bound:
            competency_form = EmployeeCompetencySelectionForm(
                initial={
                    'competencies': appraisal.competency_scores.values_list(
                        'competency_id',
                        flat=True,
                    ),
                }
            )
        return render(request, 'employee/fill_targets.html', {
            'formset': formset,
            'competency_form': competency_form,
            'appraisal': appraisal,
            'read_only': False,
            'return_history': appraisal.returns.select_related('reviewer'),
        })

    return render(request, 'employee/fill_targets.html', {
        'appraisal': appraisal,
        'read_only': True,
        'targets': appraisal.targets.all(),
        'competencies': appraisal.competency_scores.select_related('competency'),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def profile(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect('profile')
    else:
        form = UserProfileForm(instance=request.user)

    return render(request, 'account/profile.html', {'form': form})


@login_required
def change_password(request):
    from django.contrib.auth.forms import PasswordChangeForm

    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed.")
            return redirect('profile')
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'account/password_change.html', {'form': form})


@login_required
def submit_to_supervisor(request, appraisal_id):
    """Changes the status to pending_manager and assigns the supervisor."""
    appraisal = get_object_or_404(Appraisal, id=appraisal_id, employee=request.user.profile)

    if appraisal.status in ['draft', 'returned_employee']:
        if not appraisal.competency_scores.exists():
            messages.error(
                request,
                "No competencies are saved on this appraisal. Return to Edit My Goals and use "
                "'Save and Submit to Supervisor' so your competency selections are saved with the submission.",
            )
            return redirect('fill_targets')

        # 1. Look up who their supervisor is from your assignment table
        assignment = None
        if appraisal.status == 'returned_employee' and appraisal.supervisor_id:
            assignment = AssignSupervisor.objects.filter(
                employee=request.user.profile,
                supervisor=appraisal.supervisor,
            ).first()
        if assignment is None:
            assignment = AssignSupervisor.objects.filter(employee=request.user.profile).first()

        if not assignment:
            messages.error(request, "You cannot submit because no supervisor has been assigned to you yet. Contact HR.")
            return redirect('employee_dashboard')

        # 2. Stamp the supervisor onto the appraisal and lock the employee out
        appraisal.supervisor = assignment.supervisor
        appraisal.status = 'pending_manager'
        appraisal.employee_signed_at = timezone.now()
        appraisal.save()

        messages.success(request, "Appraisal submitted to your supervisor successfully!")
    
    return redirect('employee_dashboard')






# ==========================================
# SUPERVISOR DASHBOARD (DUAL-HAT)
# ==========================================
@login_required
def supervisor_dashboard(request):
    """Shows the supervisor their own appraisal AND their team's appraisals."""
    profile = request.user.profile

    # Security check
    if profile.role not in ['Supervisor', 'HOD', 'HR']: 
        messages.error(request, "Access denied.")
        return redirect('dashboard_router')

    cycle = AppraisalCycle.objects.filter(is_active=True).first()
    
    if not cycle:
        messages.error(request, "No active appraisal cycle.")
        return redirect('login')

    # 1. Get the supervisor's OWN appraisal (They are an employee too!)
    my_appraisal, created = Appraisal.objects.get_or_create(
        employee=profile,
        cycle=cycle
    )

    # 2. Get the team's appraisals waiting for review
    pending_reviews = Appraisal.objects.filter(
        supervisor=profile,
        status__in=['pending_manager', 'returned_supervisor']
    ).select_related('employee__user', 'cycle')
    assigned_employees = EmployeeProfile.objects.filter(
        employee_supervisor__supervisor=profile
    ).select_related('user', 'department').distinct()
    assigned_appraisals = Appraisal.objects.filter(
        supervisor=profile,
        cycle=cycle,
    ).select_related('employee__user', 'employee__department', 'cycle')

    context = {
        'my_appraisal': my_appraisal,
        'pending_reviews': pending_reviews,
        'assigned_employees': assigned_employees,
        'assigned_appraisals': assigned_appraisals,
    }

    return render(request, 'supervisor/dashboard.html', context)


@login_required
def supervisor_create_appraisals(request):
    """Creates draft appraisals for assigned employees selected by the supervisor."""
    profile = request.user.profile
    if profile.role != 'Supervisor':
        messages.error(request, "Only supervisors can create team appraisals.")
        return redirect('dashboard_router')

    if request.method != 'POST':
        return redirect('supervisor_dashboard')

    cycle = AppraisalCycle.objects.filter(is_active=True).first()
    if not cycle:
        messages.error(request, "No active appraisal cycle.")
        return redirect('supervisor_dashboard')

    assignments = AssignSupervisor.objects.filter(
        supervisor=profile
    ).select_related('employee')
    selection = request.POST.get('employee_selection', '')

    if selection == 'all':
        employees = [assignment.employee for assignment in assignments]
    else:
        try:
            employee_id = int(selection)
        except (TypeError, ValueError):
            messages.error(request, "Select an employee or choose all assigned employees.")
            return redirect('supervisor_dashboard')

        assignment = assignments.filter(employee_id=employee_id).first()
        if assignment is None:
            messages.error(request, "You can only create appraisals for employees assigned to you.")
            return redirect('supervisor_dashboard')
        employees = [assignment.employee]

    if not employees:
        messages.warning(request, "No employees are assigned to you.")
        return redirect('supervisor_dashboard')

    created_count = 0
    assigned_count = 0
    skipped_count = 0
    with transaction.atomic():
        for employee in employees:
            appraisal, created = Appraisal.objects.get_or_create(
                employee=employee,
                cycle=cycle,
                defaults={'supervisor': profile},
            )
            if created:
                created_count += 1
            elif appraisal.status == 'draft' and appraisal.supervisor_id is None:
                appraisal.supervisor = profile
                appraisal.save(update_fields=['supervisor'])
                assigned_count += 1
            elif appraisal.supervisor_id == profile.id:
                skipped_count += 1
            else:
                skipped_count += 1

    messages.success(
        request,
        f"Created {created_count} appraisal(s), assigned {assigned_count} existing draft(s), "
        f"and left {skipped_count} existing appraisal(s) unchanged."
    )
    return redirect('supervisor_dashboard')


@login_required
def supervisor_review(request, appraisal_id):
    """Allows the supervisor to score the employee."""
    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id, supervisor=profile)

    # SECURITY: If it's not their turn, lock it.
    if appraisal.status not in ['pending_manager', 'returned_supervisor']:
        messages.warning(request, "This appraisal is not currently pending your review.")
        return redirect('supervisor_dashboard')

    if request.method == 'POST':
        target_formset = SupervisorTargetFormSet(request.POST, instance=appraisal)
        comp_formset = SupervisorCompetencyFormSet(request.POST, instance=appraisal)

        if target_formset.is_valid() and comp_formset.is_valid():
            with transaction.atomic():
                target_formset.save()
                comp_formset.save()
            messages.success(request, "Scores saved as draft. You can submit to HOD when ready.")
            return redirect('supervisor_dashboard')
        messages.error(request, "Scores were not saved. Correct the highlighted errors and try again.")
    else:
        target_formset = SupervisorTargetFormSet(instance=appraisal)
        comp_formset = SupervisorCompetencyFormSet(instance=appraisal)

    return render(request, 'supervisor/review.html', {
        'appraisal': appraisal,
        'target_formset': target_formset,
        'comp_formset': comp_formset,
        'return_form': AppraisalReturnForm(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def supervisor_submit_to_hod(request, appraisal_id):
    """Pushes the appraisal to the HOD."""
    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id, supervisor=profile)

    if appraisal.status in ['pending_manager', 'returned_supervisor']:
        # Find the HOD for this employee's department
        from .models import AssignDepartmentHod
        hod_assignment = AssignDepartmentHod.objects.filter(department=appraisal.employee.department).first()

        if not hod_assignment:
            messages.error(request, "Cannot submit: No HOD assigned to this department yet.")
            return redirect('supervisor_dashboard')

        appraisal.hod = hod_assignment.hod
        appraisal.status = 'pending_hod'
        appraisal.supervisor_signed_at = timezone.now()
        appraisal.save()

        messages.success(request, "Appraisal successfully submitted to the HOD.")
    
    return redirect('supervisor_dashboard')


@login_required
def return_appraisal(request, appraisal_id):
    """Returns an appraisal to the previous stage with a required audit comment."""
    if request.method != 'POST':
        return redirect('dashboard_router')

    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id)

    if (
        profile.role == 'Supervisor'
        and appraisal.supervisor_id == profile.id
        and appraisal.status in ['pending_manager', 'returned_supervisor']
    ):
        stage = 'supervisor'
        destination_status = 'returned_employee'
    elif (
        profile.role == 'HOD'
        and appraisal.hod_id == profile.id
        and appraisal.status in ['pending_hod', 'returned_hod']
    ):
        stage = 'hod'
        destination_status = 'returned_supervisor'
    elif profile.role == 'HR' and appraisal.status == 'pending_hr':
        stage = 'hr'
        destination_status = 'returned_hod'
    else:
        messages.error(request, "You are not authorized to return this appraisal at its current stage.")
        return redirect('dashboard_router')

    form = AppraisalReturnForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a return comment of at least 5 characters.")
        return redirect({
            'supervisor': 'supervisor_review',
            'hod': 'hod_review',
            'hr': 'hr_approve',
        }[stage], appraisal_id=appraisal.id)

    with transaction.atomic():
        AppraisalReturn.objects.create(
            appraisal=appraisal,
            reviewer=request.user,
            reviewer_name=request.user.get_username(),
            stage=stage,
            from_status=appraisal.status,
            to_status=destination_status,
            comment=form.cleaned_data['comment'].strip(),
        )
        appraisal.status = destination_status
        appraisal.save(update_fields=['status'])

    recipient = {
        'returned_employee': 'the employee',
        'returned_supervisor': 'the supervisor',
        'returned_hod': 'the HOD',
    }[destination_status]
    messages.success(request, f"Appraisal returned to {recipient} with your comment.")
    return redirect({
        'supervisor': 'supervisor_dashboard',
        'hod': 'hod_dashboard',
        'hr': 'hr_dashboard',
    }[stage])


# Add to views.py

@login_required
def hod_dashboard(request):
    """Shows the HOD appraisals pending their review, plus their own appraisal."""
    profile = request.user.profile

    if profile.role != 'HOD':
        messages.error(request, "Access denied. You are not an HOD.")
        return redirect('dashboard_router')

    cycle = AppraisalCycle.objects.filter(is_active=True).first()
    
    # 1. Get the HOD's OWN appraisal
    my_appraisal, created = Appraisal.objects.get_or_create(
        employee=profile,
        cycle=cycle
    )

    # 2. Get appraisals waiting for HOD review
    pending_reviews = Appraisal.objects.filter(
        hod=profile,
        status__in=['pending_hod', 'returned_hod']
    ).select_related('employee__user', 'supervisor__user', 'cycle')

    context = {
        'my_appraisal': my_appraisal,
        'pending_reviews': pending_reviews
    }

    return render(request, 'hod/dashboard.html', context)


@login_required
def hod_review(request, appraisal_id):
    """Allows the HOD to view the full appraisal and add their final comments."""
    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id, hod=profile)

    # SECURITY: Only review if it is actually pending HOD
    if appraisal.status not in ['pending_hod', 'returned_hod']:
        messages.warning(request, "This appraisal is not pending your review.")
        return redirect('hod_dashboard')

    if request.method == 'POST':
        # The HOD only submits comments and pushes to HR
        form = HODCommentForm(request.POST, instance=appraisal)
        if form.is_valid():
            appraisal = form.save(commit=False)
            appraisal.status = 'pending_hr'
            appraisal.hod_signed_at = timezone.now()
            appraisal.save()
            
            messages.success(request, "Appraisal reviewed and submitted to HR successfully.")
            return redirect('hod_dashboard')
    else:
        form = HODCommentForm(instance=appraisal)

    # We need to fetch the targets and competencies to display them (read-only)
    targets = appraisal.targets.all()
    competencies = appraisal.competency_scores.all()

    return render(request, 'hod/review.html', {
        'appraisal': appraisal,
        'form': form,
        'targets': targets,
        'competencies': competencies,
        'return_form': AppraisalReturnForm(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


# Add to views.py

@login_required
def hr_dashboard(request):
    """Shows HR all appraisals pending final approval."""
    profile = request.user.profile

    if profile.role != 'HR':
        messages.error(request, "Access denied. You are not an HR Manager.")
        return redirect('dashboard_router')

    # Get all appraisals that the HOD has pushed to HR
    pending_reviews = Appraisal.objects.filter(
        status='pending_hr'
    ).select_related('employee__user', 'supervisor__user', 'hod__user', 'cycle')

    return render(request, 'hr/dashboard.html', {'pending_reviews': pending_reviews})


@login_required
def hr_supervisor_assignments(request):
    profile = request.user.profile
    if profile.role != 'HR':
        messages.error(request, "Access denied. Only HR can manage supervisor assignments.")
        return redirect('dashboard_router')

    if request.method == 'POST':
        form = HRSupervisorAssignmentForm(request.POST)
        if form.is_valid():
            department = form.cleaned_data['department']
            employee = form.cleaned_data['employee']
            supervisor = form.cleaned_data['supervisor']
            with transaction.atomic():
                AssignSupervisor.objects.filter(employee=employee).delete()
                AssignSupervisor.objects.create(
                    department=department,
                    employee=employee,
                    supervisor=supervisor,
                )
                Appraisal.objects.filter(
                    employee=employee,
                    status__in=['draft', 'returned_employee'],
                ).update(supervisor=supervisor)

            messages.success(
                request,
                f"Supervisor for {employee.user.username} was assigned to {supervisor.user.username}.",
            )
            return redirect('hr_supervisor_assignments')
    else:
        initial = {}
        department_id = request.GET.get('department')
        employee_id = request.GET.get('employee')
        if department_id and employee_id:
            assignment = AssignSupervisor.objects.filter(
                department_id=department_id,
                employee_id=employee_id,
            ).select_related('department', 'employee', 'supervisor').first()
            if assignment:
                initial = {
                    'department': assignment.department_id,
                    'employee': assignment.employee_id,
                    'supervisor': assignment.supervisor_id,
                }
        form = HRSupervisorAssignmentForm(initial=initial)

    assignments = AssignSupervisor.objects.select_related(
        'department',
        'employee__user',
        'supervisor__user',
    ).order_by('department__name', 'employee__user__username')
    return render(request, 'hr/supervisor_assignments.html', {
        'form': form,
        'assignments': assignments,
    })


@login_required
def hr_assignment_options(request):
    if request.user.profile.role != 'HR':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    department_id = request.GET.get('department_id')
    if not department_id or not Department.objects.filter(pk=department_id).exists():
        return JsonResponse({'error': 'Select a valid department.'}, status=400)

    employees = EmployeeProfile.objects.filter(
        role='Employee',
        department_id=department_id,
    ).select_related('user').order_by('user__username')
    supervisors = EmployeeProfile.objects.filter(
        role='Supervisor',
        department_id=department_id,
    ).select_related('user').order_by('user__username')

    employee_id = request.GET.get('employee_id')
    current_supervisor_id = None
    if employee_id and employees.filter(pk=employee_id).exists():
        current_supervisor_id = AssignSupervisor.objects.filter(
            employee_id=employee_id,
        ).values_list('supervisor_id', flat=True).first()

    return JsonResponse({
        'employees': [
            {
                'id': employee.id,
                'label': f"{employee.user.username} - "
                f"{employee.user.get_full_name() or employee.user.username}",
            }
            for employee in employees
        ],
        'supervisors': [
            {
                'id': supervisor.id,
                'label': f"{supervisor.user.username} - "
                f"{supervisor.user.get_full_name() or supervisor.user.username}",
            }
            for supervisor in supervisors
        ],
        'current_supervisor_id': current_supervisor_id,
    })


@login_required
def hr_approve(request, appraisal_id):
    """Allows HR to view the full appraisal, add comments, and lock it forever."""
    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id)

    if profile.role != 'HR' or appraisal.status != 'pending_hr':
        messages.warning(request, "This appraisal is not pending HR approval.")
        return redirect('hr_dashboard')

    if request.method == 'POST':
        form = HRCommentForm(request.POST, instance=appraisal)
        if form.is_valid():
            appraisal = form.save(commit=False)
            appraisal.status = 'completed'
            appraisal.hr_approved_at = timezone.now()
            
            # Calculate the final score automatically!
            appraisal.calculate_final_score()
            
            appraisal.save()
            
            messages.success(request, "Appraisal officially approved, scored, and locked.")
            return redirect('hr_dashboard')
    else:
        form = HRCommentForm(instance=appraisal)

    targets = appraisal.targets.all()
    competencies = appraisal.competency_scores.all()

    return render(request, 'hr/review.html', {
        'appraisal': appraisal,
        'form': form,
        'targets': targets,
        'competencies': competencies,
        'return_form': AppraisalReturnForm(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })