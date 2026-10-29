from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, CharField, Count, F, Q, Value, When
from django.urls import reverse
from django.http import JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages
from django.core.exceptions import ValidationError

from .models import (
    Appraisal,
    AppraisalCompetencyStatementScore,
    AppraisalCycle,
    AppNotification,
    AppraisalReturn,
    AssignSupervisor,
    Competency,
    Department,
    EmployeeProfile,
)
from .forms import (
    AppraiserReviewForm,
    AppraisalEmployeeInfoForm,
    AppraisalReturnForm,
    AppraisalTrainingFormSet,
    EmployeeCompetencySelectionForm,
    HODCommentForm,
    HRCommentForm,
    HRSupervisorAssignmentForm,
    SupervisorCompetencyFormSet,
    SupervisorCompetencyStatementFormSet,
    SupervisorTargetFormSet,
    TargetFormSet,
    UserProfileForm,
    AppraisalCycleForm
)
from .notifications import mark_as_read, notify_users


def _process_hr_supervisor_assignment(request):
    form = HRSupervisorAssignmentForm(request.POST)
    if not form.is_valid():
        return form, None, None

    department = form.cleaned_data['department']
    employee = form.cleaned_data['employee']
    supervisor = form.cleaned_data['supervisor']
    appraisals_to_transfer = Appraisal.objects.filter(
        employee=employee,
        status__in=['pending_manager', 'returned_supervisor'],
    ).exclude(supervisor=supervisor)

    if appraisals_to_transfer.exists() and request.POST.get('confirm_transfer') != '1':
        confirmation = {
            'department_id': department.id,
            'employee_id': employee.id,
            'supervisor_id': supervisor.id,
            'employee_name': employee.user.get_full_name() or employee.user.username,
            'supervisor_name': supervisor.user.get_full_name() or supervisor.user.username,
            'appraisal_count': appraisals_to_transfer.count(),
        }
        return form, confirmation, None

    previous_assignment = AssignSupervisor.objects.filter(
        employee=employee,
    ).select_related('supervisor__user').first()
    previous_supervisor = (
        previous_assignment.supervisor
        if previous_assignment and previous_assignment.supervisor_id != supervisor.id
        else None
    )
    supervisor_changed = previous_assignment is None or previous_assignment.supervisor_id != supervisor.id

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
        transferred_count = 0
        if request.POST.get('confirm_transfer') == '1':
            transferred_count = appraisals_to_transfer.update(supervisor=supervisor)

        if supervisor_changed:
            notified_users = [employee.user, supervisor.user]
            if previous_supervisor:
                notified_users.append(previous_supervisor.user)
            previous_name = (
                previous_supervisor.user.get_full_name() or previous_supervisor.user.username
                if previous_supervisor
                else 'no previous supervisor'
            )
            notify_users(
                notified_users,
                title='Supervisor assignment updated',
                message=(
                    f"Supervisor for {employee.user.get_full_name() or employee.user.username} "
                    f"changed from {previous_name} to "
                    f"{supervisor.user.get_full_name() or supervisor.user.username}."
                ),
                url=reverse('dashboard_router'),
            )

    return form, None, (
        employee.user.username,
        supervisor.user.username,
        transferred_count,
    )


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard_router')
    return render(request, 'home.html')


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

    elif request.user.is_superuser: 
        return redirect("/admin")

    else:
        return redirect('employee_dashboard') 
    

@login_required
def employee_dashboard(request):
    """Shows the employee all active appraisal cycles and their full appraisal history."""
    profile = request.user.profile
    active_cycles = list(
        AppraisalCycle.objects.filter(is_active=True).order_by('-year')[:1]
    )

    assignment = AssignSupervisor.objects.filter(
        employee=profile,
    ).select_related('supervisor__user').first()
    assigned_supervisor = assignment.supervisor if assignment else None

    active_appraisal_cards = []
    for active_cycle in active_cycles:
        appraisal, _ = Appraisal.objects.get_or_create(
            employee=profile,
            cycle=active_cycle,
        )
        if appraisal.status in ['draft', 'returned_employee']:
            if assigned_supervisor and appraisal.supervisor != assigned_supervisor:
                appraisal.supervisor = assigned_supervisor
                appraisal.save(update_fields=['supervisor'])

        supervisor_for_card = appraisal.supervisor or assigned_supervisor
        latest_return = appraisal.returns.first()
        progress_stage, progress_percent = {
            'draft': (1, 0),
            'returned_employee': (1, 0),
            'pending_manager': (2, 20),
            'returned_supervisor': (2, 20),
            'pending_hod': (3, 40),
            'returned_hod': (3, 40),
            'pending_hr': (4, 60),
            'completed': (5, 100),
        }[appraisal.status]
        active_appraisal_cards.append({
            'cycle': active_cycle,
            'appraisal': appraisal,
            'supervisor': supervisor_for_card,
            'latest_return': latest_return,
            'is_editable': appraisal.status in ['draft', 'returned_employee'],
            'is_completed': appraisal.status == 'completed',
            'progress_stage': progress_stage,
            'progress_percent': progress_percent,
        })
        
    if not active_cycles:
        messages.info(request, "No active appraisal cycle right now.")

    all_appraisals = (
        Appraisal.objects.filter(employee=profile)
        .select_related('cycle', 'supervisor__user', 'employee_department')
        .order_by('-cycle__year', '-pk')
    )

    primary_card = active_appraisal_cards[0] if active_appraisal_cards else None
    active_appraisal_count = len(active_appraisal_cards)
    incomplete_appraisal_count = sum(
        card['appraisal'].status in ['draft', 'returned_employee']
        for card in active_appraisal_cards
    )
    submitted_appraisal_count = sum(
        card['appraisal'].employee_signed_at is not None
        for card in active_appraisal_cards
    )

    return render(request, 'employee/dashboard.html', {
        'active_cycles': active_cycles,
        'active_appraisal_cards': active_appraisal_cards,
        'active_appraisal_count': active_appraisal_count,
        'incomplete_appraisal_count': incomplete_appraisal_count,
        'submitted_appraisal_count': submitted_appraisal_count,
        'assigned_supervisor': assigned_supervisor,
        'all_appraisals': all_appraisals,
        # Backward compatibility for single-cycle contexts
        'cycle': primary_card['cycle'] if primary_card else None,
        'appraisal': primary_card['appraisal'] if primary_card else None,
        'latest_return': primary_card['latest_return'] if primary_card else None,
    })



@login_required
def fill_targets(request, year=None):
    """Lets employees edit draft goals and view their appraisal after submission."""
    profile = request.user.profile
    if year:
        cycle = get_object_or_404(AppraisalCycle, year=year, is_active=True)
    else:
        cycle = AppraisalCycle.objects.filter(is_active=True).order_by('-year').first()

    if not cycle:
        messages.error(request, "No active appraisal cycle right now.")
        return redirect('employee_dashboard')

    appraisal, _ = Appraisal.objects.get_or_create(employee=profile, cycle=cycle)
    cycle_fill_url = reverse('fill_targets_year', args=[cycle.year])

    # Completed or locked appraisals are read-only, but the fill-targets route still renders them.
    if appraisal.status not in ['draft', 'returned_employee']:
        if request.method == 'POST':
            messages.warning(request, "This appraisal is completed and locked from editing.")
            return redirect(cycle_fill_url)
        return _render_read_only_appraisal(request, appraisal, allow_pdf=False)

    def save_competency_selections(competency_form):
        selected_statement_values = competency_form.cleaned_data['competency_statements']
        selected_statement_map = {
            statement['value']: (
                group['competency'],
                index,
                statement['text'],
            )
            for group in competency_form.competency_groups
            for index, statement in enumerate(group['statements'])
        }
        selected_statements = [
            selected_statement_map[value]
            for value in selected_statement_values
        ]
        if not selected_statement_values:
            selected_statements = [
                (competency, index, statement)
                for competency in competency_form.cleaned_data['competencies']
                for index, statement in enumerate(competency.description_items)
            ]
        selected_competencies = {
            competency.id: competency
            for competency, _, _ in selected_statements
        }

        selected_ids = list(selected_competencies)
        appraisal.competency_scores.exclude(
            competency_id__in=selected_ids,
        ).delete()
        for competency in selected_competencies.values():
            competency_score, _ = appraisal.competency_scores.get_or_create(
                competency=competency,
            )
            selected_for_competency = [
                (index, statement)
                for selected_competency, index, statement in selected_statements
                if selected_competency.id == competency.id
            ]
            selected_indexes = [index for index, _ in selected_for_competency]
            competency_score.statement_scores.exclude(
                statement_index__in=selected_indexes,
            ).delete()
            for index, statement in selected_for_competency:
                statement_score, created = competency_score.statement_scores.get_or_create(
                    statement_index=index,
                    defaults={'statement': statement},
                )
                if not created and statement_score.statement != statement:
                    statement_score.statement = statement
                    statement_score.end_year_score = None
                    statement_score.save(update_fields=['statement', 'end_year_score'])

    try:
        current_step = int(request.GET.get('step', 0) or 0)
    except (TypeError, ValueError):
        current_step = 0
    if current_step not in [0, 1, 2, 3, 4]:
        current_step = 0

    if request.method == 'POST':
        formset = TargetFormSet(request.POST, instance=appraisal)
        competency_form = EmployeeCompetencySelectionForm(request.POST)
        employee_info_form = AppraisalEmployeeInfoForm(
            request.POST,
            instance=appraisal,
            profile=profile,
            prefix='employee_info',
        )
        training_formset = AppraisalTrainingFormSet(
            request.POST,
            instance=appraisal,
            prefix='trainings',
        )
        action = request.POST.get('action')
        action_steps = {
            'save_personal': 0,
            'save_targets': 1,
            'save_core_competencies': 2,
            'save_competencies': 4,
            'submit': 4,
        }
        current_step = action_steps.get(action, current_step)
        step_for_action = {
            'save_personal': 1,
            'save_targets': 2,
            'save_core_competencies': 3,
            'save_competencies': 4,
        }.get(action)

        if action == 'save_personal':
            if employee_info_form.is_valid() and training_formset.is_valid():
                with transaction.atomic():
                    employee_info_form.save()
                    training_formset.save()
                messages.success(request, "Personal details and training saved.")
                return redirect(f"{cycle_fill_url}?step={step_for_action}")
        elif action == 'save_targets':
            if formset.is_valid():
                formset.save()
                messages.success(request, "Performance planning goals saved.")
                return redirect(f"{cycle_fill_url}?step={step_for_action}")
        elif action in ['save_core_competencies', 'save_competencies']:
            save_all_sections = action == 'save_competencies'
            if (
                competency_form.is_valid()
                and employee_info_form.is_valid()
                and (
                    not save_all_sections
                    or (
                        formset.is_valid()
                        and training_formset.is_valid()
                    )
                )
            ):
                with transaction.atomic():
                    employee_info_form.save()
                    if save_all_sections:
                        formset.save()
                        training_formset.save()
                    save_competency_selections(competency_form)
                messages.success(request, "Competency selections and appraisal comments saved.")
                return redirect(f"{cycle_fill_url}?step={step_for_action}")
        elif (
            formset.is_valid()
            and competency_form.is_valid()
            and employee_info_form.is_valid()
            and training_formset.is_valid()
        ):
            submit_for_review = action == 'submit'
            if submit_for_review and not employee_info_form.cleaned_data.get('employee_comments'):
                employee_info_form.add_error(
                    'employee_comments',
                    'Add your overall assessment comments before submitting.',
                )
            has_selected_statements = bool(
                competency_form.cleaned_data['competency_statements']
                or competency_form.cleaned_data['competencies']
            )
            assignment = None

            if submit_for_review and employee_info_form.errors:
                messages.error(request, "Add your overall assessment comments before submitting.")
            elif submit_for_review and not has_selected_statements:
                messages.error(request, "Select at least one competency statement before submitting your appraisal.")
            elif submit_for_review:
                assignment = AssignSupervisor.objects.filter(
                    employee=profile,
                ).select_related('supervisor').first()
                if not assignment:
                    messages.error(
                        request,
                        "You cannot submit because no supervisor has been assigned to you yet. Contact HR.",
                    )

            if (
                not employee_info_form.errors
                and (not submit_for_review or (has_selected_statements and assignment))
            ):
                with transaction.atomic():
                    employee_info_form.save()
                    formset.save()
                    training_formset.save()
                    save_competency_selections(competency_form)

                    if assignment:
                        appraisal.supervisor = assignment.supervisor
                        appraisal.status = 'pending_manager'
                        appraisal.employee_signed_at = timezone.now()
                        appraisal.save(update_fields=[
                            'supervisor',
                            'status',
                            'employee_signed_at',
                        ])
                        notify_users(
                            [assignment.supervisor.user],
                            title='Appraisal awaiting your review',
                            message=(
                                f"{profile.user.get_full_name() or profile.user.username} "
                                f"submitted the {appraisal.cycle.year} appraisal for your review."
                            ),
                            url=reverse('supervisor_review', args=[appraisal.id]),
                        )

                if assignment:
                    messages.success(request, "Appraisal saved and submitted to your supervisor.")
                else:
                    messages.success(request, "Targets and competency selections saved successfully.")
                return redirect('employee_dashboard')
    else:
        formset = TargetFormSet(instance=appraisal)
        employee_info_form = AppraisalEmployeeInfoForm(
            instance=appraisal,
            profile=profile,
            prefix='employee_info',
        )
        training_formset = AppraisalTrainingFormSet(
            instance=appraisal,
            prefix='trainings',
        )
        competency_form = EmployeeCompetencySelectionForm(
            initial={
                'competencies': appraisal.competency_scores.values_list(
                    'competency_id',
                    flat=True,
                ),
                'competency_statements': [
                    f'{competency_score.competency_id}:{statement_score.statement_index}'
                    for competency_score in appraisal.competency_scores.prefetch_related(
                        'statement_scores',
                    )
                    for statement_score in competency_score.statement_scores.all()
                ],
            }
        )

    assignment = AssignSupervisor.objects.filter(
        employee=profile,
    ).select_related('supervisor__user').first()
    if not competency_form.is_bound:
        competency_form = EmployeeCompetencySelectionForm(
            initial={
                'competencies': appraisal.competency_scores.values_list(
                    'competency_id',
                    flat=True,
                ),
                'competency_statements': [
                    f'{competency_score.competency_id}:{statement_index}'
                    for competency_score in appraisal.competency_scores.prefetch_related(
                        'statement_scores',
                    )
                    for statement_index in (
                        list(competency_score.statement_scores.values_list(
                            'statement_index',
                            flat=True,
                        ))
                        or list(range(len(competency_score.competency.description_items)))
                    )
                ],
            }
        )
    selected_statement_ids = set(
        competency_form['competency_statements'].value() or []
    )
    if request.method == 'POST' and request.POST.get('action') == 'save_competencies':
        if any(formset.errors) or formset.non_form_errors():
            current_step = 1
        elif training_formset.errors or training_formset.non_form_errors():
            current_step = 0
        elif 'employee_comments' in employee_info_form.errors:
            current_step = 3
        elif employee_info_form.errors:
            current_step = 0
        elif competency_form.errors:
            current_step = 3
    elif request.method == 'POST' and request.POST.get('action') == 'submit':
        if any(formset.errors) or formset.non_form_errors():
            current_step = 1
        elif 'employee_comments' in employee_info_form.errors:
            current_step = 3
        elif (
            employee_info_form.errors
            or training_formset.errors
            or training_formset.non_form_errors()
        ):
            current_step = 0
        else:
            current_step = 4
    return render(request, 'employee/fill_targets.html', {
        'formset': formset,
        'employee_info_form': employee_info_form,
        'training_formset': training_formset,
        'competency_form': competency_form,
        'competency_groups': competency_form.competency_groups,
        'core_competency_groups': [
            group for group in competency_form.competency_groups
            if group['competency'].competency_type == 'core'
        ],
        'non_core_competency_groups': [
            group for group in competency_form.competency_groups
            if group['competency'].competency_type == 'non_core'
        ],
        'selected_statement_ids': selected_statement_ids,
        'score_breakdown': appraisal.get_score_breakdown(),
        'appraisal': appraisal,
        'cycle_fill_url': cycle_fill_url,
        'current_step': current_step,
        'assigned_supervisor': (
            assignment.supervisor if assignment else appraisal.supervisor
        ),
        'read_only': False,
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def profile(request):
    employee_profile = request.user.profile
    if request.method == 'POST':
        form = UserProfileForm(
            request.POST,
            instance=request.user,
            profile=employee_profile,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect('profile')
    else:
        form = UserProfileForm(instance=request.user, profile=employee_profile)

    return render(request, 'account/profile.html', {'form': form})


@login_required
def notifications(request):
    notification_list = AppNotification.objects.filter(recipient=request.user)
    page = Paginator(notification_list, 20).get_page(request.GET.get('page', 1))
    return render(request, 'account/notifications.html', {
        'notification_page': page,
        'unread_notification_count': notification_list.filter(
            read_at__isnull=True,
        ).count(),
    })


@login_required
def open_notification(request, notification_id):
    if request.method != 'POST':
        return redirect('notifications')

    notification = get_object_or_404(
        AppNotification,
        pk=notification_id,
        recipient=request.user,
    )
    mark_as_read(notification)
    destination = notification.url
    if not destination or not url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        destination = reverse('dashboard_router')
    return redirect(destination)

def delete_noti(request,noti_id): 
    get_not=AppNotification.objects.get(id=noti_id)  
    get_not.delete()  
    messages.error(request,'deleted')
    return redirect('notifications')


@login_required
def mark_all_notifications_read(request):
    if request.method == 'POST':
        AppNotification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
    return redirect('notifications')


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
        if not appraisal.targets.exists():
            messages.error(
                request,
                "Add at least one key result area and target before submitting your appraisal.",
            )
            return redirect('fill_targets_year', year=appraisal.cycle.year)
        if not appraisal.employee_comments:
            messages.error(request, "Add your overall assessment comments before submitting.")
            return redirect('fill_targets_year', year=appraisal.cycle.year)
        if not appraisal.competency_scores.exists():
            messages.error(
                request,
                "No competencies are saved on this appraisal. Return to Edit My Goals and use "
                "'Save and Submit to Supervisor' so your competency selections are saved with the submission.",
            )
            return redirect('fill_targets_year', year=appraisal.cycle.year)

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
        notify_users(
            [assignment.supervisor.user],
            title='Appraisal awaiting your review',
            message=(
                f"{request.user.get_full_name() or request.user.username} "
                f"submitted the {appraisal.cycle.year} appraisal for your review."
            ),
            url=reverse('supervisor_review', args=[appraisal.id]),
        )

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
    
    # if not cycle:
    #     messages.error(request, "No active appraisal cycle.")
    #     return redirect('login')

    # A supervisor only has an own appraisal when they are assigned to a supervisor.
    has_supervisor = AssignSupervisor.objects.filter(employee=profile).exists()
    my_appraisal = None
    if has_supervisor:
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
    ).select_related('employee__user', 'employee__department', 'cycle').order_by(
        '-cycle__year',
        'employee__user__username',
    )

    context = {
        'cycle': cycle,
        'my_appraisal': my_appraisal,
        'has_supervisor': has_supervisor,
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
    statement_scores = AppraisalCompetencyStatementScore.objects.filter(
        competency_score__appraisal=appraisal,
    ).select_related('competency_score__competency')

    # SECURITY: If it's not their turn, lock it.
    if appraisal.status not in ['pending_manager', 'returned_supervisor']:
        messages.warning(request, "This appraisal is not currently pending your review.")
        return redirect('supervisor_dashboard')

    if request.method == 'POST':
        target_formset = SupervisorTargetFormSet(request.POST, instance=appraisal)
        appraiser_form = AppraiserReviewForm(request.POST, instance=appraisal)
        comp_formset = SupervisorCompetencyFormSet(request.POST, instance=appraisal)
        statement_score_formset = SupervisorCompetencyStatementFormSet(
            request.POST,
            queryset=statement_scores,
            prefix='statement_scores',
        )

        if (
            target_formset.is_valid()
            and appraiser_form.is_valid()
            and comp_formset.is_valid()
            and statement_score_formset.is_valid()
        ):
            submit_for_review = request.POST.get('action') == 'submit'
            if submit_for_review and not appraiser_form.cleaned_data.get('supervisor_comments'):
                appraiser_form.add_error(
                    'supervisor_comments',
                    "Add the appraiser's comments before submitting this appraisal.",
                )
            if submit_for_review and not appraiser_form.cleaned_data.get('assessment_decision'):
                appraiser_form.add_error(
                    'assessment_decision',
                    'Select an assessment decision before submitting this appraisal.',
                )
            if appraiser_form.errors:
                messages.error(request, "Complete the required appraisal comments and decision before submitting.")
            else: 
                with transaction.atomic():
                    target_formset.save()
                    appraiser_form.save()
                    comp_formset.save()
                    statement_score_formset.save()
                if submit_for_review:
                    messages.success(request, "Review saved. Submitting the appraisal to the HOD.")
                    return redirect('supervisor_submit_to_hod', appraisal_id=appraisal.id)
                messages.success(request, "Scores and appraisal comments saved as draft.")
                return redirect('supervisor_dashboard')
        messages.error(request, "Scores were not saved. Correct the highlighted errors and try again.")
    else:
        target_formset = SupervisorTargetFormSet(instance=appraisal)
        appraiser_form = AppraiserReviewForm(instance=appraisal)
        comp_formset = SupervisorCompetencyFormSet(instance=appraisal)
        statement_score_formset = SupervisorCompetencyStatementFormSet(
            queryset=statement_scores,
            prefix='statement_scores',
        )

    statement_forms_by_competency_score = {}
    for statement_form in statement_score_formset.forms:
        statement_forms_by_competency_score.setdefault(
            statement_form.instance.competency_score_id,
            [], 
        ).append(statement_form)
    competency_groups = [
        {
            'form': competency_form,
            'statements': statement_forms_by_competency_score.get(
                competency_form.instance.pk,
                [],
            ),
            'weight': (
                '0.3'
                if competency_form.instance.competency.competency_type == 'core'
                else '0.1'
            ),
            'type': competency_form.instance.competency.competency_type,
        }
        for competency_form in comp_formset.forms
    ]

    return render(request, 'supervisor/review.html', {
        'appraisal': appraisal,
        'target_formset': target_formset,
        'appraiser_form': appraiser_form,
        'comp_formset': comp_formset,
        'statement_score_formset': statement_score_formset,
        'competency_groups': competency_groups,
        'trainings': appraisal.trainings.all(),
        'return_form': AppraisalReturnForm(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def supervisor_submit_to_hod(request, appraisal_id):
    """Pushes the appraisal to the HOD."""
    profile = request.user.profile
    appraisal = get_object_or_404(Appraisal, id=appraisal_id, supervisor=profile)

    if appraisal.status in ['pending_manager', 'returned_supervisor']:
        if not appraisal.supervisor_comments or not appraisal.assessment_decision:
            messages.error(
                request,
                "Complete the appraiser's comments and assessment decision before submitting to the HOD.",
            )
            return redirect('supervisor_review', appraisal_id=appraisal.id)
        try:
            appraisal.validate_final_score_inputs()
        except ValidationError as error:
            messages.error(request, error.messages[0])
            return redirect('supervisor_review', appraisal_id=appraisal.id)

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
        notify_users(
            [hod_assignment.hod.user],
            title='Appraisal awaiting your review',
            message=(
                f"{appraisal.employee.user.get_full_name() or appraisal.employee.user.username}'s "
                f"{appraisal.cycle.year} appraisal is ready for your review."
            ),
            url=reverse('hod_review', args=[appraisal.id]),
        )

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

    return_recipients = {
        'returned_employee': appraisal.employee.user,
        'returned_supervisor': appraisal.supervisor.user if appraisal.supervisor_id else None,
        'returned_hod': appraisal.hod.user if appraisal.hod_id else None,
    }
    return_destinations = {
        'returned_employee': reverse('employee_dashboard'),
        'returned_supervisor': reverse('supervisor_dashboard'),
        'returned_hod': reverse('hod_dashboard'),
    }
    notify_users(
        [return_recipients[destination_status]],
        title='Appraisal returned for updates',
        message=(
            f"{appraisal.employee.user.get_full_name() or appraisal.employee.user.username}'s "
            f"{appraisal.cycle.year} appraisal was returned by {request.user.username}."
        ),
        url=return_destinations[destination_status],
    )

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
    
    my_appraisal = None
    if cycle:
        my_appraisal, created = Appraisal.objects.get_or_create(
            employee=profile,
            cycle=cycle,
        )

    # 2. Get appraisals waiting for HOD review
    pending_reviews = Appraisal.objects.filter(
        hod=profile,
        status__in=['pending_hod', 'returned_hod']
    ).select_related(
        'employee__user',
        'employee__department',
        'employee_department',
        'supervisor__user',
        'cycle',
    )

    historical_appraisals = Appraisal.objects.filter(
        hod=profile,
        status__in=['pending_hr', 'completed']
    ).select_related(
        'employee__user',
        'employee_department',
        'supervisor__user',
        'hod__user',
        'cycle',
    ).order_by('-cycle__year', 'employee__user__username')
    context = {
        'cycle': cycle,
        'my_appraisal': my_appraisal,
        'pending_reviews': pending_reviews,
        'historical_appraisals': historical_appraisals,
    }

    return render(request, 'hod/dashboard.html', context)


def _render_read_only_appraisal(request, appraisal, *, allow_pdf=False):
    return render(request, 'employee/fill_targets.html', {
        'appraisal': appraisal,
        'read_only': True,
        'print_mode': False,
        'allow_pdf': allow_pdf,
        'trainings': appraisal.trainings.all(),
        'targets': appraisal.targets.all(),
        'competencies': appraisal.competency_scores.select_related(
            'competency',
        ).prefetch_related('statement_scores'),
        'score_breakdown': appraisal.get_score_breakdown(),
        'return_history': appraisal.returns.select_related('reviewer'),
        'assigned_supervisor': appraisal.supervisor,
    })


@login_required
def supervisor_view_appraisal(request, appraisal_id):
    profile = request.user.profile
    if profile.role != 'Supervisor':
        messages.error(request, "Access denied. Only the assigned supervisor can view this appraisal.")
        return redirect('dashboard_router')

    appraisal = get_object_or_404(
        Appraisal.objects.select_related(
            'employee__user',
            'employee_department',
            'supervisor__user',
            'hod__user',
            'cycle',
        ),
        pk=appraisal_id,
        supervisor=profile,
    )
    return _render_read_only_appraisal(request, appraisal)


@login_required
def hod_view_appraisal(request, appraisal_id):
    profile = request.user.profile
    if profile.role != 'HOD':
        messages.error(request, "Access denied. Only the assigned HOD can view this appraisal.")
        return redirect('dashboard_router')

    appraisal = get_object_or_404(
        Appraisal.objects.select_related(
            'employee__user',
            'employee_department',
            'supervisor__user',
            'hod__user',
            'cycle',
        ),
        pk=appraisal_id,
        hod=profile,
    )
    return _render_read_only_appraisal(request, appraisal)

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
            hr_profiles = EmployeeProfile.objects.filter(
                role='HR',
            ).select_related('user')
            notify_users(
                [hr_profile.user for hr_profile in hr_profiles],
                title='Appraisal awaiting HR approval',
                message=(
                    f"{appraisal.employee.user.get_full_name() or appraisal.employee.user.username}'s "
                    f"{appraisal.cycle.year} appraisal was submitted for final approval."
                ),
                url=reverse('hr_dashboard'),
            )
            
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
        'score_breakdown': appraisal.get_score_breakdown(),
        'trainings': appraisal.trainings.all(),
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

    users = EmployeeProfile.objects.filter(
        role__in=['Employee', 'Supervisor', 'HOD']
    ).select_related('user').order_by('user__username')
    role_choices = [
        choice for choice in EmployeeProfile.ROLE_CHOICES
        if choice[0] != 'HR'
    ]

    if request.method == 'POST':
        if request.POST.get('assignment_action') == 'save':
            assignment_form, transfer_confirmation, saved_assignment = (
                _process_hr_supervisor_assignment(request)
            )
            if saved_assignment:
                employee_username, supervisor_username, transferred_count = saved_assignment
                messages.success(
                    request,
                    f"Supervisor for {employee_username} was assigned to {supervisor_username}.",
                )
                if transferred_count:
                    messages.success(
                        request,
                        f"Transferred {transferred_count} in-progress appraisal(s). "
                        "Existing scores, comments, and return history were preserved.",
                    )
                return redirect(f"{reverse('hr_dashboard')}#assignments")
        else:
            assignment_form = HRSupervisorAssignmentForm()
            transfer_confirmation = None

        if request.POST.get('assignment_action') != 'save':
            assignment_form = HRSupervisorAssignmentForm()
            transfer_confirmation = None

        if request.POST.get('assignment_action') != 'save':
            roles_dashboard_url = f"{reverse('hr_dashboard')}#roles"
            submitted_roles = {}
            allowed_profile_ids = {str(user.id) for user in users}
            for key, role in request.POST.items():
                if not key.startswith('role_'):
                    continue
                profile_id = key.removeprefix('role_')
                if profile_id not in allowed_profile_ids or role not in dict(role_choices):
                    messages.error(request, "Choose a valid user and role.")
                    return redirect(roles_dashboard_url)
                submitted_roles[profile_id] = role

            if not submitted_roles:
                messages.error(request, "No user roles were submitted.")
                return redirect(roles_dashboard_url)

            blocked_role_changes = []
            for user_profile in users:
                new_role = submitted_roles.get(str(user_profile.id))
                if user_profile.role != 'Supervisor' or not new_role or new_role == 'Supervisor':
                    continue

                pending_count = Appraisal.objects.filter(
                    supervisor=user_profile,
                    status__in=['pending_manager', 'returned_supervisor'],
                ).count()
                has_assignments = AssignSupervisor.objects.filter(
                    supervisor=user_profile,
                ).exists()
                if pending_count or has_assignments:
                    reason = []
                    if pending_count:
                        reason.append(f'{pending_count} appraisal(s) awaiting review')
                    if has_assignments:
                        reason.append('employee assignments')
                    blocked_role_changes.append(
                        f"{user_profile.user.username} has " + ' and '.join(reason)
                    )

            if blocked_role_changes:
                messages.error(
                    request,
                    "Reassign the following supervisor workload before changing roles: "
                    + '; '.join(blocked_role_changes) + '.',
                )
                return redirect(roles_dashboard_url)

            changed_count = 0
            with transaction.atomic():
                for user_profile in users:
                    new_role = submitted_roles.get(str(user_profile.id))
                    if new_role and user_profile.role != new_role:
                        old_role = user_profile.get_role_display()
                        user_profile.role = new_role
                        user_profile.save(update_fields=['role'])
                        notify_users(
                            [user_profile.user],
                            title='Your account role was updated',
                            message=(
                                f"Your role was changed from {old_role} to "
                                f"{user_profile.get_role_display()} by HR."
                            ),
                            url=reverse('dashboard_router'),
                        )
                        changed_count += 1

            if changed_count:
                messages.success(request, f"Updated {changed_count} user role(s).")
            else:
                messages.info(request, "No user roles needed updating.")
            return redirect(roles_dashboard_url)

    # Get all appraisals that the HOD has pushed to HR
    pending_reviews = Appraisal.objects.filter(
        status='pending_hr'
    ).select_related('employee__user', 'supervisor__user', 'hod__user', 'cycle')

    cycles = AppraisalCycle.objects.order_by('-year')
    departments = Department.objects.order_by('name')
    selected_cycle = request.GET.get('cycle', '').strip()
    available_cycle_years = set(cycles.values_list('year', flat=True))
    if selected_cycle:
        try:
            selected_cycle_year = int(selected_cycle)
        except ValueError:
            selected_cycle_year = None
        if selected_cycle_year not in available_cycle_years:
            selected_cycle_year = None
    else:
        selected_cycle_year = AppraisalCycle.objects.filter(
            is_active=True,
        ).values_list('year', flat=True).first()

    selected_department = request.GET.get('department', '').strip()
    if selected_department and not departments.filter(pk=selected_department).exists():
        selected_department = ''

    dashboard_appraisals = Appraisal.objects.all()
    if selected_cycle_year is not None:
        dashboard_appraisals = dashboard_appraisals.filter(
            cycle__year=selected_cycle_year,
        )
    if selected_department:
        dashboard_appraisals = dashboard_appraisals.filter(
            Q(employee_department_id=selected_department)
            | Q(employee_department__isnull=True, employee__department_id=selected_department),
        )

    status_counts = dashboard_appraisals.aggregate(
        total=Count('pk'),
        draft=Count('pk', filter=Q(status='draft')),
        supervisor=Count('pk', filter=Q(status__in=['pending_manager', 'returned_supervisor'])),
        hod=Count('pk', filter=Q(status__in=['pending_hod', 'returned_hod'])),
        hr=Count('pk', filter=Q(status='pending_hr')),
        returned_employee=Count('pk', filter=Q(status='returned_employee')),
        completed=Count('pk', filter=Q(status='completed')),
    )
    department_rows = dashboard_appraisals.annotate(
        department_label=Case(
            When(employee_department__isnull=False, then=F('employee_department__name')),
            When(employee_department_name__gt=Value(''), then=F('employee_department_name')),
            When(employee__department__isnull=False, then=F('employee__department__name')),
            default=Value('Unassigned'),
            output_field=CharField(),
        ),
    ).values('department_label').annotate(
        total=Count('pk'),
        completed=Count('pk', filter=Q(status='completed')),
    ).order_by('department_label')
    department_chart = [
        {
            'name': row['department_label'],
            'completed': row['completed'],
            'in_progress': row['total'] - row['completed'],
        }
        for row in department_rows
    ]

    return render(request, 'hr/dashboard.html', {
        'pending_reviews': pending_reviews,
        'departments': departments,
        'cycles': cycles,
        'users': users,
        'role_choices': role_choices,
        'supervisor_assignment_form': (
            assignment_form
            if request.method == 'POST' and request.POST.get('assignment_action') == 'save'
            else HRSupervisorAssignmentForm()
        ),
        'transfer_confirmation': (
            transfer_confirmation
            if request.method == 'POST' and request.POST.get('assignment_action') == 'save'
            else None
        ),
        'dashboard_filters': {
            'cycle': selected_cycle_year,
            'department': selected_department,
        },
        'dashboard_counts': status_counts,
        'workflow_chart': [
            {'label': 'Draft', 'count': status_counts['draft']},
            {'label': 'Supervisor review', 'count': status_counts['supervisor']},
            {'label': 'HOD review', 'count': status_counts['hod']},
            {'label': 'HR approval', 'count': status_counts['hr']},
            {'label': 'Returned to employee', 'count': status_counts['returned_employee']},
            {'label': 'Completed', 'count': status_counts['completed']},
        ],
        'department_chart': department_chart,
    })


@login_required
def hr_search_completed_appraisals(request):
    if request.user.profile.role != 'HR':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    query = request.GET.get('q', '').strip()[:100]
    department_id = request.GET.get('department', '').strip()
    cycle_year = request.GET.get('cycle_year', '').strip()
    appraisals = Appraisal.objects.filter(status='completed').select_related(
        'employee__user',
        'employee__department',
        'supervisor__user',
        'hod__user',
        'cycle',
    )

    if department_id:
        try:
            department_id = int(department_id)
        except ValueError:
            return JsonResponse({'error': 'Invalid department filter.'}, status=400)
        department = get_object_or_404(Department, pk=department_id)
        appraisals = appraisals.filter(employee_department_id=department_id)

    if cycle_year:
        try:
            cycle_year = int(cycle_year)
        except ValueError:
            return JsonResponse({'error': 'Invalid cycle year filter.'}, status=400)
        appraisals = appraisals.filter(cycle__year=cycle_year)

    if query:
        appraisals = appraisals.filter(
            Q(employee__user__username__icontains=query)
            | Q(employee__user__first_name__icontains=query)
            | Q(employee__user__last_name__icontains=query)
            | Q(employee__user__email__icontains=query)
            | Q(employee_department__name__icontains=query)
            | Q(employee_department_name__icontains=query)
            | Q(supervisor__user__username__icontains=query)
            | Q(supervisor__user__first_name__icontains=query)
            | Q(supervisor__user__last_name__icontains=query)
            | Q(hod__user__username__icontains=query)
            | Q(hod__user__first_name__icontains=query)
            | Q(hod__user__last_name__icontains=query)
            | Q(cycle__year__icontains=query)
            | Q(final_rating__icontains=query)
        )

    paginator = Paginator(
        appraisals.order_by('-cycle__year', 'employee__user__username'),
        25,
    )
    page = paginator.get_page(request.GET.get('page', 1))
    results = []
    for appraisal in page.object_list:
        results.append({
            'id': appraisal.id,
            'employee_staff_id': appraisal.employee.user.username,
            'employee_name': appraisal.employee.user.get_full_name()
            or appraisal.employee.user.username,
            'department': appraisal.employee_department_name
            or (appraisal.employee_department.name if appraisal.employee_department else '—'),
            'supervisor': appraisal.supervisor.user.username
            if appraisal.supervisor else '—',
            'hod': appraisal.hod.user.username if appraisal.hod else '—',
            'cycle_year': appraisal.cycle.year,
            'overall_percentage': str(appraisal.overall_percentage)
            if appraisal.overall_percentage is not None else '—',
            'final_rating': appraisal.final_rating or '—',
            'view_url': reverse('hr_completed_appraisal', args=[appraisal.id]),
        })

    return JsonResponse({
        'results': results,
        'page': page.number,
        'pages': paginator.num_pages,
        'total': paginator.count,
        'has_previous': page.has_previous(),
        'has_next': page.has_next(),
    })


@login_required
def hr_completed_appraisal(request, appraisal_id):
    if request.user.profile.role != 'HR':
        messages.error(request, "Access denied. You are not an HR Manager.")
        return redirect('dashboard_router')

    appraisal = get_object_or_404(
        Appraisal.objects.select_related(
            'employee__user',
            'employee__department',
            'supervisor__user',
            'hod__user',
            'cycle',
        ),
        pk=appraisal_id,
        status='completed',
    )
    return render(request, 'hr/completed_appraisal.html', {
        'appraisal': appraisal,
        'targets': appraisal.targets.all(),
        'competencies': appraisal.competency_scores.select_related('competency'),
        'score_breakdown': appraisal.get_score_breakdown(),
        'trainings': appraisal.trainings.all(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def employee_appraisal_detail(request, appraisal_id):
    """Read-only view for an employee to inspect a submitted, past, or completed appraisal."""
    appraisal = get_object_or_404(
        Appraisal.objects.select_related(
            'employee__user',
            'employee_department',
            'supervisor__user',
            'hod__user',
            'cycle',
        ),
        pk=appraisal_id,
        employee=request.user.profile,
    )
    return _render_read_only_appraisal(request, appraisal, allow_pdf=True)


@login_required
def employee_appraisal_pdf(request, appraisal_id):
    appraisal = get_object_or_404(
        Appraisal.objects.select_related(
            'employee__user',
            'employee__department',
            'supervisor__user',
            'hod__user',
            'cycle',
        ),
        pk=appraisal_id,
        employee=request.user.profile,
        status='completed',
    )
    return render(request, 'employee/fill_targets.html', {
        'appraisal': appraisal,
        'read_only': True,
        'print_mode': True,
        'trainings': appraisal.trainings.all(),
        'targets': appraisal.targets.all(),
        'competencies': appraisal.competency_scores.select_related(
            'competency',
        ).prefetch_related('statement_scores'),
        'score_breakdown': appraisal.get_score_breakdown(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })


@login_required
def hr_supervisor_assignments(request):
    profile = request.user.profile
    if profile.role != 'HR':
        messages.error(request, "Access denied. Only HR can manage supervisor assignments.")
        return redirect('dashboard_router')

    if request.method == 'POST':
        form, transfer_confirmation, saved_assignment = _process_hr_supervisor_assignment(request)
        if transfer_confirmation:
            return render(request, 'hr/supervisor_assignments.html', {
                'form': form,
                'departments': Department.objects.order_by('name'),
                'transfer_confirmation': transfer_confirmation,
            })
        if saved_assignment:
            employee_username, supervisor_username, transferred_count = saved_assignment
            messages.success(
                request,
                f"Supervisor for {employee_username} was assigned to {supervisor_username}.",
            )
            if transferred_count:
                messages.success(
                    request,
                    f"Transferred {transferred_count} in-progress appraisal(s). "
                    "Existing scores, comments, and return history were preserved.",
                )
            return redirect('hr_supervisor_assignments')
        return render(request, 'hr/supervisor_assignments.html', {
            'form': form,
            'departments': Department.objects.order_by('name'),
        })
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

    return render(request, 'hr/supervisor_assignments.html', {
        'form': form,
        'departments': Department.objects.order_by('name'),
    })


@login_required
def hr_search_supervisor_assignments(request):
    if request.user.profile.role != 'HR':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    query = request.GET.get('q', '').strip()[:100]
    department_id = request.GET.get('department', '').strip()
    assignments = AssignSupervisor.objects.select_related(
        'department',
        'employee__user',
        'supervisor__user',
    )

    if department_id:
        try:
            department_id = int(department_id)
        except ValueError:
            return JsonResponse({'error': 'Invalid department filter.'}, status=400)
        assignments = assignments.filter(department_id=department_id)

    if query:
        assignments = assignments.filter(
            Q(employee__user__username__icontains=query)
            | Q(employee__user__first_name__icontains=query)
            | Q(employee__user__last_name__icontains=query)
            | Q(employee__user__email__icontains=query)
            | Q(supervisor__user__username__icontains=query)
            | Q(supervisor__user__first_name__icontains=query)
            | Q(supervisor__user__last_name__icontains=query)
            | Q(department__name__icontains=query)
        )

    paginator = Paginator(
        assignments.order_by('department__name', 'employee__user__username'),
        25,
    )
    page = paginator.get_page(request.GET.get('page', 1))
    results = []
    for assignment in page.object_list:
        employee = assignment.employee
        supervisor = assignment.supervisor
        results.append({
            'department': assignment.department.name,
            'employee_id': employee.id,
            'employee_staff_id': employee.user.username,
            'employee_name': employee.user.get_full_name() or employee.user.username,
            'supervisor_staff_id': supervisor.user.username,
            'supervisor_name': supervisor.user.get_full_name() or supervisor.user.username,
            'department_id': assignment.department_id,
            'supervisor_id': supervisor.id,
            'edit_url': (
                f"{reverse('hr_supervisor_assignments')}?"
                f"department={assignment.department_id}&employee={employee.id}"
            ),
        })

    return JsonResponse({
        'results': results,
        'page': page.number,
        'pages': paginator.num_pages,
        'total': paginator.count,
        'has_previous': page.has_previous(),
        'has_next': page.has_next(),
    })


@login_required
def hr_assignment_options(request):
    if request.user.profile.role != 'HR':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    department_id = request.GET.get('department_id')
    try:
        department_id = int(department_id)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Select a valid department.'}, status=400)

    if not Department.objects.filter(pk=department_id).exists():
        return JsonResponse({'error': 'Select a valid department.'}, status=400)

    employees = EmployeeProfile.objects.filter(
        role__in=['Employee', 'Supervisor'],
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
            try:
                appraisal.calculate_final_score()
            except ValidationError as error:
                messages.error(request, error.messages[0])
                return redirect('hr_approve', appraisal_id=appraisal.id)
            
            appraisal.save()
            
            notify_users(
                [appraisal.employee.user],
                title='Appraisal completed',
                message=f"Your {appraisal.cycle.year} appraisal has been approved and completed.",
                url=reverse('employee_dashboard'),
            )
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
        'score_breakdown': appraisal.get_score_breakdown(),
        'trainings': appraisal.trainings.all(),
        'return_form': AppraisalReturnForm(),
        'return_history': appraisal.returns.select_related('reviewer'),
    })

@login_required
def newcycle(request):
    if request.user.profile.role != 'HR':
        messages.error(request, "Access denied. Only HR can create appraisal cycles.")
        return redirect('dashboard_router')

    form = AppraisalCycleForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            cycle = form.save(commit=False)
            if cycle.is_active:
                AppraisalCycle.objects.exclude(pk=cycle.pk).update(is_active=False)
            cycle.save()
        messages.success(request, f"Appraisal cycle {cycle.year} was created successfully.")
        return redirect('new_app_cycle')

    return render(request, 'hr/new_cycle.html', {
        'form': form,
        'cycles': AppraisalCycle.objects.annotate(
            appraisal_count=Count('appraisals'),
        ),
    })


@login_required
def set_appraisal_cycle_status(request, cycle_id):
    if request.user.profile.role != 'HR':
        messages.error(request, "Access denied. Only HR can manage appraisal cycles.")
        return redirect('dashboard_router')
    if request.method != 'POST':
        messages.error(request, "Use the cycle controls to change its status.")
        return redirect('new_app_cycle')

    cycle = get_object_or_404(AppraisalCycle, pk=cycle_id)
    activate = request.POST.get('is_active') == 'true'
    with transaction.atomic():
        if activate:
            AppraisalCycle.objects.exclude(pk=cycle.pk).update(is_active=False)
        cycle.is_active = activate
        cycle.save(update_fields=['is_active'])

    if activate:
        messages.success(request, f"Appraisal cycle {cycle.year} is now active.")
    else:
        messages.success(request, f"Appraisal cycle {cycle.year} is now inactive.")
    return redirect('new_app_cycle')


@login_required
def delete_appraisal_cycle(request, cycle_id):
    if request.user.profile.role != 'HR':
        messages.error(request, "Access denied. Only HR can manage appraisal cycles.")
        return redirect('dashboard_router')
    if request.method != 'POST':
        messages.error(request, "Use the cycle controls to delete a cycle.")
        return redirect('new_app_cycle')

    cycle = get_object_or_404(AppraisalCycle, pk=cycle_id)
    if cycle.appraisals.exists():
        messages.error(
            request,
            f"Cycle {cycle.year} cannot be deleted because it has appraisal records. "
            "Deactivate it instead to preserve those records.",
        )
        return redirect('new_app_cycle')

    year = cycle.year
    cycle.delete()
    messages.success(request, f"Appraisal cycle {year} was deleted.")
    return redirect('new_app_cycle')


def changer_user_role(request):
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        new_role = request.POST.get('new_role')

        if not user_id or not new_role:
            return JsonResponse({'error': 'Missing user_id or new_role.'}, status=400)

        try:
            user_profile = EmployeeProfile.objects.get(pk=user_id)
        except EmployeeProfile.DoesNotExist:
            return JsonResponse({'error': 'User not found.'}, status=404)

        if new_role not in dict(EmployeeProfile.ROLE_CHOICES):
            return JsonResponse({'error': 'Invalid role.'}, status=400)

        user_profile.role = new_role
        user_profile.save()

        return JsonResponse({'success': True, 'new_role': new_role})

    return JsonResponse({'error': 'Invalid request method.'}, status=405) 
