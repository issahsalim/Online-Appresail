from datetime import datetime

from django.test import TestCase
from django.contrib.auth.models import User
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from .audit import reset_audit_request_context, set_audit_request_context

from .forms import EmployeeCompetencySelectionForm
from .models import (
    Appraisal,
    AppraisalCycle,
    AppraisalCompetencyScore,
    AppraisalCompetencyStatementScore,
    AppNotification,
    AuditLog,
    AppraisalReturn,
    AppraisalTarget,
    AppraisalTraining,
    AssignDepartmentHod,
    DepartmentSupervisor,
    Competency,
    Department,
    EmployeeProfile,
    SupervisorReporting,
)


class EmployeeAppraisalVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='staff-001', password='test-password')
        self.profile = EmployeeProfile.objects.create(user=self.user)
        supervisor_user = User.objects.create_user(
            username='supervisor-001',
            first_name='Morgan',
            last_name='Supervisor',
            email='morgan@example.com',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            job_title='Team Lead',
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(
            employee=self.profile,
            supervisor=self.supervisor,
            cycle=cycle,
            status='pending_manager',
            supervisor_comments='Good progress.',
        )
        AppraisalTarget.objects.create(
            appraisal=self.appraisal,
            key_result_area='Improve service',
            target_description='Reduce response time.',
            mid_year_progress='achieved',
            end_year_score=4,
        )
        competency = Competency.objects.create(
            name='Teamwork',
            competency_type='core',
            description='Work effectively with colleagues toward shared goals.',
        )
        self.appraisal.competency_scores.create(
            competency=competency,
            mid_year_progress='well_exhibited',
            end_year_score=5,
        )
        self.client.force_login(self.user)

    def test_employee_can_view_review_scores_after_submission_without_editing(self):
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Supervisor Review')
        self.assertNotContains(response, 'pending_manager')
        self.assertContains(response, 'Improve service')
        self.assertContains(response, 'Target achieved')
        self.assertContains(response, 'Good progress.')
        self.assertContains(response, 'SECTION 1 - B: Appraiser’s Information')
        self.assertContains(response, 'Morgan Supervisor')
        self.assertNotContains(response, 'supervisor-001')
        self.assertContains(response, 'Position:')
        self.assertContains(response, 'Team Lead')
        self.assertContains(response, 'morgan@example.com')
        self.assertContains(response, 'Work effectively with colleagues toward shared goals.')
        self.assertContains(response, '>4</td>')
        self.assertContains(response, '>5</td>')
        self.assertNotContains(response, 'End-year score:</strong>')
        self.assertContains(response, 'SECTION 5: Annual Appraisal')

        self.assertContains(response, 'M — A × 0.6:')
        self.assertContains(response, 'N — Average of core competency group averages:')
        self.assertContains(response, 'T — M + N + O:')
        self.assertContains(response, '2.40')
        self.assertContains(response, '1.50')
        self.assertContains(response, '3.90')
        self.assertContains(response, '78.00%')

        response = self.client.post(reverse('fill_targets'), {
            'targets-TOTAL_FORMS': '1',
            'targets-INITIAL_FORMS': '1',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '1000',
        })

        self.assertRedirects(
            response,
            reverse('fill_targets_year', args=[self.appraisal.cycle.year]),
        )
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.targets.get().end_year_score, 4)

    def test_completed_appraisal_print_view_shows_save_as_pdf_and_all_score_sections(self):
        self.appraisal.status = 'completed'
        self.appraisal.save(update_fields=['status'])
        AppraisalTraining.objects.create(
            appraisal=self.appraisal,
            institution='Yeji Training Centre',
            date=datetime(2026, 6, 15),
            programme='Patient care workshop',
        )
        AppraisalReturn.objects.create(
            appraisal=self.appraisal,
            reviewer_name='Morgan Supervisor',
            stage='supervisor',
            from_status='pending_manager',
            to_status='returned_employee',
            comment='Please revise the returned section.',
        )

        response = self.client.get(
            reverse('employee_appraisal_pdf', args=[self.appraisal.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Please revise the returned section.')
        self.assertContains(response, 'Download / Save as PDF')
        self.assertContains(response, 'SECTION 1 - A: Appraisee’s Personal Information')
        self.assertContains(response, 'TRAINING RECEIVED DURING THE YEAR UNDER REVIEW')
        self.assertContains(response, 'Patient care workshop')
        self.assertContains(response, 'Morgan Supervisor')
        self.assertNotContains(response, 'supervisor-001')
        self.assertContains(response, 'Position:')
        self.assertContains(response, 'SECTION 2: Performance Planning Form')
        self.assertContains(response, 'appraisal-planning-paper')
        self.assertContains(response, '@page appraisal-planning-landscape')
        self.assertContains(response, '@page appraisal-review-landscape')
        self.assertContains(response, 'size: A4 landscape')
        self.assertContains(response, 'table-layout: fixed !important')
        self.assertContains(response, 'appraisal-midyear-paper')
        self.assertContains(response, 'appraisal-endyear-paper')
        endyear_start = response.content.decode().index('SECTION 4: End-of-Year Review Form')
        calculation_start = response.content.decode().index('Q — Sum of end-year target scores:', endyear_start)
        next_section_start = response.content.decode().index(
            'SECTION 5: Annual Appraisal — Core Competencies',
            calculation_start,
        )
        self.assertLess(endyear_start, calculation_start)
        self.assertLess(calculation_start, next_section_start)
        self.assertContains(response, 'SECTION 3: Mid-Year Review Form')
        self.assertContains(response, 'SECTION 4: End-of-Year Review Form')
        self.assertContains(response, 'SECTION 5: Annual Appraisal — Core Competencies')
        self.assertContains(response, 'SECTION 5: Annual Appraisal (Continuation) — Non-Core Competencies')
        self.assertNotContains(response, 'Sections 6–10: Comments and assessment')
        self.assertContains(response, 'SECTION 6: Annual Appraisal')
        self.assertContains(response, 'SECTION 7: Career Development')
        self.assertContains(response, 'SECTION 8: Assessment Decision')
        self.assertContains(response, 'SECTION 9: Appraisee’s Comments on Overall Assessment')
        self.assertContains(response, 'SECTION 10: Head of Department’s / Unit’s Comments on Overall Assessment')
        self.assertContains(response, 'Final HR Approval')
        self.assertContains(response, 'Overall Performance Rating')
        self.assertContains(response, '@page { size: A4 portrait;')
        self.assertContains(response, 'overflow: visible !important')
        self.assertContains(response, 'break-inside: avoid !important')
        self.assertContains(response, 'page-break-inside: avoid !important')
        self.assertContains(response, 'min-width: 0 !important')
        self.assertContains(response, 'background-color: #a43a10 !important')
        self.assertContains(response, 'print-color-adjust: exact !important')

        detail_response = self.client.get(
            reverse('employee_appraisal_detail', args=[self.appraisal.id]),
        )
        self.assertContains(detail_response, 'Please revise the returned section.')

    def test_dashboard_progress_reflects_each_appraisal_stage(self):
        expected_progress = {
            'draft': '0',
            'returned_employee': '0',
            'pending_manager': '20',
            'returned_supervisor': '20',
            'pending_hod': '40',
            'returned_hod': '40',
            'pending_hr': '60',
            'completed': '100',
        }

        for status, percent in expected_progress.items():
            with self.subTest(status=status):
                self.appraisal.status = status
                self.appraisal.save(update_fields=['status'])

                response = self.client.get(reverse('employee_dashboard'))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'{percent}%')
                self.assertContains(response, f'aria-valuenow="{percent}"')

    def test_dashboard_appraisal_card_shows_friendly_status_and_submission_date(self):
        self.appraisal.status = 'pending_manager'
        self.appraisal.employee_signed_at = timezone.make_aware(datetime(2026, 10, 5))
        self.appraisal.save(update_fields=['status', 'employee_signed_at'])

        response = self.client.get(reverse('employee_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending Supervisor Review')
        self.assertContains(response, '5 October 2026')
        self.assertNotContains(response, 'SUBMITTED:</strong> Morgan Supervisor')
        self.assertContains(response, 'Total appraisals')
        self.assertContains(response, 'Incomplete')
        self.assertContains(response, 'Submitted')
        self.assertContains(response, '20% complete')
        self.assertContains(
            response,
            'body:has(.role-dashboard-shell, .hr-dashboard-shell) > .navbar-appraisal',
        )

    def test_mobile_dashboard_navigation_has_vertical_animated_toggle(self):
        response = self.client.get(reverse('employee_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'aria-controls="employee-dashboard-navigation"',
        )
        self.assertContains(
            response,
            '.role-sidebar-nav { display: grid; gap: .55rem; grid-template-columns: minmax(0, 1fr); }',
        )
        self.assertContains(response, '.role-dashboard-sidebar.is-expanded .role-sidebar-nav')
        self.assertContains(response, 'grid-template-columns: minmax(0, 1fr)')
        self.assertContains(response, 'transform: translateX(-105%)')
        self.assertContains(response, 'role-sidebar-backdrop')
        self.assertContains(response, '.role-sidebar-brand { display: none; }')
        self.assertContains(response, 'role-sidebar-drawer-brand')
        self.assertContains(response, 'Mathias Catholic Hospital<small>EMPLOYEE WORKSPACE</small>')
        self.assertContains(response, 'data-sidebar-close')
        self.assertContains(response, '.role-dashboard-content { padding: 4.75rem 1.1rem 1.1rem; }')
        self.assertContains(response, 'top: 4.5rem;')
        self.assertContains(response, "event.key === 'Escape'")
        self.assertContains(response, 'navigation.inert = mobile && !expanded')

    def test_active_dashboard_shows_only_the_newest_active_cycle(self):
        newer_cycle = AppraisalCycle.objects.create(year=2027, is_active=True)
        older_cycle = self.appraisal.cycle

        response = self.client.get(reverse('employee_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['active_appraisal_cards']), 1)
        self.assertEqual(
            response.context['active_appraisal_cards'][0]['cycle'],
            newer_cycle,
        )
        self.assertContains(response, '2027 Appraisal')
        self.assertNotContains(response, '2026 Appraisal')
        self.assertContains(response, 'Cycle 2026')
        self.assertTrue(
            Appraisal.objects.filter(employee=self.profile, cycle=older_cycle).exists()
        )

    def test_dashboard_history_includes_cycles_without_an_existing_appraisal(self):
        older_cycle = self.appraisal.cycle
        self.appraisal.delete()
        newer_cycle = AppraisalCycle.objects.create(year=2027, is_active=True)

        response = self.client.get(reverse('employee_dashboard'))

        self.assertEqual(response.status_code, 200)
        history_cycles = {
            appraisal.cycle_id
            for appraisal in response.context['all_appraisals']
        }
        self.assertEqual(
            history_cycles,
            {older_cycle.id, newer_cycle.id},
        )
        self.assertContains(response, 'Cycle 2026')
        self.assertContains(response, 'Cycle 2027')
        self.assertContains(
            response,
            reverse('fill_targets_year', args=[older_cycle.year]),
        )
        self.assertContains(
            response,
            reverse('fill_targets_year', args=[newer_cycle.year]),
        )

    def test_returned_appraisal_from_inactive_cycle_can_be_edited_and_saved(self):
        self.appraisal.status = 'returned_employee'
        self.appraisal.cycle.is_active = False
        self.appraisal.cycle.save(update_fields=['is_active'])
        self.appraisal.save(update_fields=['status'])

        response = self.client.get(
            reverse('fill_targets_year', args=[self.appraisal.cycle.year]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['read_only'])
        self.assertContains(response, 'Review and resubmit')

        response = self.client.post(
            reverse('fill_targets_year', args=[self.appraisal.cycle.year]),
            {
                'action': 'save_personal',
                'employee_info-employee_comments': 'I updated my returned appraisal.',
                'trainings-TOTAL_FORMS': '4',
                'trainings-INITIAL_FORMS': '0',
                'trainings-MIN_NUM_FORMS': '0',
                'trainings-MAX_NUM_FORMS': '4',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=1",
        )
        self.appraisal.refresh_from_db()
        self.assertEqual(
            self.appraisal.employee_comments,
            'I updated my returned appraisal.',
        )
        self.assertEqual(self.appraisal.status, 'returned_employee')

    def test_history_keeps_editable_appraisals_available_from_every_cycle(self):
        older_cycle = AppraisalCycle.objects.create(year=2025, is_active=False)
        older_appraisal = Appraisal.objects.create(
            employee=self.profile,
            cycle=older_cycle,
            status='returned_employee',
        )

        response = self.client.get(reverse('employee_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cycle 2025')
        self.assertContains(
            response,
            reverse('fill_targets_year', args=[older_appraisal.cycle.year]),
        )
        self.assertContains(response, 'Review and resubmit')

    def test_employee_sees_message_when_supervisor_is_not_assigned(self):
        self.appraisal.supervisor = None
        self.appraisal.save(update_fields=['supervisor'])

        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A supervisor has not been assigned yet.')

    def test_employee_sees_assigned_supervisor_details_while_editing_goals(self):
        department = Department.objects.create(name='Operations')
        self.profile.department = department
        self.profile.save(update_fields=['department'])
        self.supervisor.department = department
        self.supervisor.save(update_fields=['department'])
        self.appraisal.supervisor = None
        self.appraisal.status = 'draft'
        self.appraisal.save(update_fields=['supervisor', 'status'])
        DepartmentSupervisor.objects.create(
            department=department,
            supervisor=self.supervisor,
        )

        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['read_only'])
        self.assertContains(response, 'SECTION 1 - B: Appraiser’s Information')
        self.assertContains(response, 'Morgan Supervisor')
        self.assertNotContains(response, 'supervisor-001')
        self.assertContains(response, 'Position:')
        self.assertContains(response, 'Team Lead')
        self.assertContains(response, 'morgan@example.com')

    def test_editable_appraisal_uses_form_branding_and_horizontal_steps(self):
        self.appraisal.status = 'draft'
        self.appraisal.save(update_fields=['status'])

        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MATHIAS CATHOLIC HOSPITAL, YEJI')
        self.assertContains(response, '/static/images/ghana-coat-of-arms.png')
        self.assertContains(response, '/static/images/mathias-catholic-hospital-logo.png')
        self.assertContains(response, 'SECTION 2: Performance Planning Form')
        self.assertContains(response, 'data-step-nav="0"')
        self.assertContains(response, 'data-step-nav="1"')
        self.assertContains(response, 'data-step-nav="2"')
        self.assertContains(response, 'data-step-nav="3"')
        self.assertContains(response, 'data-step-nav="4"')
        self.assertContains(response, '/ 5 pages')
        self.assertContains(response, 'appraisal-target-entry-table')
        self.assertContains(response, 'appraisal-mobile-field-label')
        self.assertContains(response, 'flex-wrap: nowrap')
        self.assertContains(response, 'Save and continue')

    def test_preview_is_a_separate_live_read_only_final_step(self):
        self.appraisal.status = 'draft'
        self.appraisal.employee_first_name = 'Alex'
        self.appraisal.employee_last_name = 'Morgan'
        self.appraisal.employee_comments = 'I achieved my service goals.'
        self.appraisal.save(update_fields=[
            'status',
            'employee_first_name',
            'employee_last_name',
            'employee_comments',
        ])

        response = self.client.get(reverse('fill_targets_year', args=[2026]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review your appraisal before submitting')
        self.assertContains(response, 'data-step-panel="4"')
        self.assertContains(response, 'data-preview-name')
        self.assertContains(response, 'data-preview-targets')
        self.assertContains(response, 'data-preview-training')
        self.assertContains(response, 'data-preview-core')
        self.assertContains(response, 'data-preview-non-core')
        self.assertContains(response, 'data-preview-comments')
        self.assertContains(response, 'updateSubmissionPreview')
        content = response.content.decode()
        editable_step = content.index('data-step-panel="3"')
        preview_step = content.index('data-step-panel="4"')
        self.assertNotIn(
            'Review your appraisal before submitting',
            content[editable_step:preview_step],
        )
        preview_content = content[preview_step:]
        self.assertIn('Review your appraisal before submitting', preview_content)
        self.assertIn('SECTION 2: Performance Planning Form', preview_content)
        self.assertIn('SECTION 5: Annual Appraisal — Core Competencies', preview_content)
        self.assertIn('SECTION 5: Annual Appraisal — Non-Core Competencies', preview_content)

    def test_core_and_non_core_competencies_are_separate_form_steps(self):
        self.appraisal.status = 'draft'
        self.appraisal.save(update_fields=['status'])
        response = self.client.get(reverse('fill_targets'))
        content = response.content.decode()
        core_step = content.index('data-step-panel="2"')
        non_core_step = content.index('data-step-panel="3"')

        self.assertLess(
            content.index('Core Competencies', core_step),
            non_core_step,
        )
        self.assertGreater(
            content.index('Non-Core Competencies', non_core_step),
            non_core_step,
        )
        self.assertContains(response, 'background: linear-gradient(90deg, var(--form-rust-dark), var(--form-rust))')

    def test_saving_employee_information_moves_to_performance_planning(self):
        self.appraisal.status = 'draft'
        self.appraisal.save(update_fields=['status'])

        response = self.client.post(reverse('fill_targets'), {
            'action': 'save_personal',
            'employee_info-employee_comments': 'I met my service goals.',
            'trainings-TOTAL_FORMS': '4',
            'trainings-INITIAL_FORMS': '0',
            'trainings-MIN_NUM_FORMS': '0',
            'trainings-MAX_NUM_FORMS': '4',
        })

        self.assertRedirects(response, f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=1")
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.employee_comments, 'I met my service goals.')

    def test_saving_a_specific_cycle_keeps_the_employee_on_that_cycle(self):
        self.appraisal.status = 'draft'
        self.appraisal.save(update_fields=['status'])
        next_cycle = AppraisalCycle.objects.create(year=2027, is_active=True)
        next_appraisal = Appraisal.objects.create(
            employee=self.profile,
            cycle=next_cycle,
        )
        data = {
            'action': 'save_targets',
            'targets-TOTAL_FORMS': '1',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '1000',
            'targets-0-key_result_area': '2026 service improvement',
            'targets-0-target_description': 'Reduce response time.',
            'targets-0-resources_required': 'Staff training',
        }

        response = self.client.post(
            reverse('fill_targets_year', args=[self.appraisal.cycle.year]),
            data,
        )

        self.assertRedirects(
            response,
            f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=2",
        )
        self.assertTrue(
            self.appraisal.targets.filter(
                key_result_area='2026 service improvement',
            ).exists()
        )
        self.assertFalse(next_appraisal.targets.exists())


class PublicHomePageTests(TestCase):
    def test_homepage_shows_public_welcome_content_and_assets(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recognising the people behind every act of care.')
        self.assertContains(response, 'How the appraisal works')
        self.assertContains(response, 'images/heroImage1.webp')
        self.assertContains(response, 'images/staff-colla1.webp')
        self.assertContains(response, reverse('login'))
        self.assertContains(response, 'html { overflow-x: clip;')
        self.assertContains(response, '.section-pad .row.g-5')

    def test_signed_in_user_is_sent_to_their_role_dashboard(self):
        user = User.objects.create_user(username='home-user', password='test-password')
        EmployeeProfile.objects.create(user=user, role='Employee')
        self.client.force_login(user)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard_router'))


class LoginPageTests(TestCase):
    def test_login_page_displays_hospital_design_and_authentication_fields(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'images/loginimg.webp')
        self.assertContains(response, 'Welcome back')
        self.assertContains(response, 'Staff ID')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_invalid_login_displays_authentication_error(self):
        response = self.client.post(reverse('login'), {
            'username': 'unknown-staff',
            'password': 'incorrect-password',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct')

    def test_authenticated_navbar_provides_working_logout_action(self):
        user = User.objects.create_user(
            username='navbar-user',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=user, role='Employee')
        self.client.force_login(user)

        response = self.client.get(reverse('profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'navbar-account-toggle')
        self.assertContains(response, 'navbar-account-menu')
        self.assertContains(response, 'Staff ID')
        self.assertContains(response, 'navbar-user')
        self.assertContains(response, 'navbar-account-logout')
        self.assertContains(response, 'My Profile')
        self.assertContains(response, 'Log out')
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

        response = self.client.post(reverse('logout'))

        self.assertRedirects(response, settings.LOGOUT_REDIRECT_URL)
        self.assertNotIn('_auth_user_id', self.client.session)


class AccountProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='profile-user',
            password='Original-password-123!',
        )
        self.profile = EmployeeProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_user_can_update_personal_profile_fields(self):
        response = self.client.post(reverse('profile'), {
            'first_name': 'Jordan',
            'last_name': 'Smith',
            'email': 'jordan@example.com',
            'title': 'ms',
            'other_title': '',
            'other_names': 'Taylor',
            'gender': 'female',
            'job_title': 'Analyst',
            'appointment_to_present_grade_date': '2020-04-15',
        })

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Jordan')
        self.assertEqual(self.user.last_name, 'Smith')
        self.assertEqual(self.user.email, 'jordan@example.com')
        self.assertEqual(self.user.username, 'profile-user')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.title, 'ms')
        self.assertEqual(self.profile.other_names, 'Taylor')
        self.assertEqual(self.profile.gender, 'female')
        self.assertEqual(self.profile.job_title, 'Analyst')
        self.assertEqual(
            str(self.profile.appointment_to_present_grade_date),
            '2020-04-15',
        )

    def test_password_change_requires_correct_current_password_and_keeps_user_logged_in(self):
        url = reverse('password_change')
        response = self.client.post(url, {
            'old_password': 'incorrect-current-password',
            'new_password1': 'New-secure-password-123!',
            'new_password2': 'New-secure-password-123!',
        })

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Original-password-123!'))

        response = self.client.post(url, {
            'old_password': 'Original-password-123!',
            'new_password1': 'New-secure-password-123!',
            'new_password2': 'New-secure-password-123!',
        })

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-secure-password-123!'))


class AuditLogAccessTests(TestCase):
    def setUp(self):
        self.hr_user = User.objects.create_user(
            username='audit-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=self.hr_user, role='HR')
        self.superadmin = User.objects.create_superuser(
            username='audit-superadmin',
            email='superadmin@example.com',
            password='test-password',
        )

    def test_profile_changes_record_actor_and_request_ip(self):
        self.client.force_login(self.hr_user)

        response = self.client.post(reverse('profile'), {
            'first_name': 'Audit',
            'last_name': 'User',
            'email': 'audit@example.com',
            'title': '',
            'other_title': '',
            'other_names': '',
            'gender': '',
            'job_title': 'Audit Officer',
            'appointment_to_present_grade_date': '',
        }, REMOTE_ADDR='203.0.113.15')

        self.assertRedirects(response, reverse('profile'))
        entry = AuditLog.objects.get(
            actor=self.hr_user,
            model_name='app.EmployeeProfile',
        )
        self.assertEqual(entry.actor_username, 'audit-hr')
        self.assertEqual(entry.actor_role, 'HR')
        self.assertEqual(entry.ip_address, '203.0.113.15')
        self.assertIn('job_title', entry.changes)
        self.assertNotIn('Audit Officer', str(entry.changes))

    def test_successful_login_is_audited_with_request_ip(self):
        response = self.client.post(reverse('login'), {
            'username': 'audit-hr',
            'password': 'test-password',
        }, REMOTE_ADDR='203.0.113.20')

        self.assertEqual(response.status_code, 302)
        entry = AuditLog.objects.get(actor=self.hr_user, action='login')
        self.assertEqual(entry.ip_address, '203.0.113.20')

    def test_appraisal_submission_note_names_the_assigned_supervisor(self):
        employee_user = User.objects.create_user(
            username='audit-employee-submitter',
            first_name='Employee',
            last_name='Submitter',
            password='test-password',
        )
        employee = EmployeeProfile.objects.create(user=employee_user, role='Employee')
        supervisor_user = User.objects.create_user(
            username='audit-supervisor',
            first_name='Casey',
            last_name='Supervisor',
            password='test-password',
        )
        supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
        )
        cycle = AppraisalCycle.objects.create(year=2026)
        appraisal = Appraisal.objects.create(
            employee=employee,
            supervisor=supervisor,
            cycle=cycle,
            status='draft',
        )

        request = RequestFactory().post('/employee/submit/')
        request.user = employee_user
        token = set_audit_request_context(request)
        try:
            appraisal.status = 'pending_manager'
            appraisal.save(update_fields=['status'])
        finally:
            reset_audit_request_context(token)

        entry = AuditLog.objects.get(
            model_name='app.Appraisal',
            object_id=str(appraisal.pk),
            action='updated',
        )
        self.assertEqual(
            entry.note,
            'Employee Submitter submitted the 2026 appraisal to supervisor Casey Supervisor.',
        )

    def test_hr_cannot_see_superadmin_audit_entries_but_superadmin_can(self):
        AuditLog.objects.create(
            actor=self.hr_user,
            actor_username='audit-hr',
            actor_role='HR',
            action='updated',
            model_name='app.EmployeeProfile',
            object_id='1',
            note='HR updated an employee profile.',
            actor_is_superuser=False,
        )
        AuditLog.objects.create(
            actor=self.superadmin,
            actor_username='audit-superadmin',
            action='updated',
            model_name='app.Competency',
            object_id='2',
            note='Superadmin updated a competency.',
            actor_is_superuser=True,
        )

        self.client.force_login(self.hr_user)
        response = self.client.get(reverse('audit_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'audit-hr')
        self.assertContains(response, 'HR updated an employee profile.')
        self.assertNotContains(response, 'audit-superadmin')
        self.assertNotContains(response, 'Superadmin updated a competency.')

        self.client.force_login(self.superadmin)
        response = self.client.get(reverse('audit_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'audit-hr')
        self.assertContains(response, 'audit-superadmin')
        self.assertContains(response, 'Superadmin updated a competency.')

    def test_non_hr_users_cannot_access_audit_log(self):
        employee = User.objects.create_user(
            username='audit-employee',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=employee, role='Employee')
        self.client.force_login(employee)

        response = self.client.get(reverse('audit_log'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard_router'))

    def test_audit_log_admin_is_read_only_and_superadmin_only(self):
        audit_admin = admin.site._registry[AuditLog]
        hr_request = RequestFactory().get('/admin/app/auditlog/')
        hr_request.user = self.hr_user
        superadmin_request = RequestFactory().get('/admin/app/auditlog/')
        superadmin_request.user = self.superadmin

        self.assertFalse(audit_admin.has_view_permission(hr_request))
        self.assertTrue(audit_admin.has_view_permission(superadmin_request))
        self.assertFalse(audit_admin.has_add_permission(superadmin_request))
        self.assertTrue(audit_admin.has_change_permission(superadmin_request))
        self.assertFalse(audit_admin.has_delete_permission(superadmin_request))


class InAppNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='notification-user',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=self.user, role='Employee')
        self.other_user = User.objects.create_user(
            username='other-notification-user',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=self.other_user, role='Employee')
        self.client.force_login(self.user)

    def test_navbar_shows_unread_count_and_recent_notifications(self):
        AppNotification.objects.create(
            recipient=self.user,
            title='Unread update',
            message='There is a new appraisal update.',
        )
        AppNotification.objects.create(
            recipient=self.user,
            title='Already read',
            message='This was read.',
            read_at=timezone.now(),
        )
        AppNotification.objects.create(
            recipient=self.other_user,
            title='Private update',
            message='Only another user should see this.',
        )

        response = self.client.get(reverse('profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 unread')
        self.assertContains(response, 'Unread update')
        self.assertNotContains(response, 'Private update')

    def test_opening_notification_marks_only_own_notification_read(self):
        notification = AppNotification.objects.create(
            recipient=self.user,
            title='Review request',
            message='An appraisal is ready.',
            url=reverse('employee_dashboard'),
        )

        response = self.client.get(
            reverse('open_notification', args=[notification.id]),
        )
        self.assertRedirects(response, reverse('notifications'))
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)

        response = self.client.post(
            reverse('open_notification', args=[notification.id]),
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

        self.client.force_login(self.other_user)
        response = self.client.post(
            reverse('open_notification', args=[notification.id]),
        )
        self.assertEqual(response.status_code, 404)

    def test_opening_notification_rejects_external_redirects(self):
        notification = AppNotification.objects.create(
            recipient=self.user,
            title='Unsafe link',
            message='This link must not redirect externally.',
            url='https://example.com',
        )

        response = self.client.post(
            reverse('open_notification', args=[notification.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard_router'))

    def test_mark_all_read_only_updates_signed_in_users_notifications(self):
        own_notification = AppNotification.objects.create(
            recipient=self.user,
            title='Own notice',
            message='Unread',
        )
        other_notification = AppNotification.objects.create(
            recipient=self.other_user,
            title='Other notice',
            message='Unread',
        )

        response = self.client.post(reverse('mark_all_notifications_read'))

        self.assertRedirects(response, reverse('notifications'))
        own_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertIsNotNone(own_notification.read_at)
        self.assertIsNone(other_notification.read_at)

    def test_role_change_notifies_affected_user(self):
        hr_user = User.objects.create_user(
            username='notification-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=hr_user, role='HR')
        self.client.force_login(hr_user)

        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.user.profile.id}': 'HOD',
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#roles")
        notification = AppNotification.objects.get(recipient=self.user)
        self.assertEqual(notification.title, 'Your account role was updated')
        self.assertIn('Employee to Head of Department', notification.message)

    def test_supervisor_change_notifies_employee_old_and_new_supervisors(self):
        hr_user = User.objects.create_user(
            username='assignment-notification-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=hr_user, role='HR')
        department = Department.objects.create(name='Notification Department')
        self.user.profile.department = department
        self.user.profile.save(update_fields=['department'])
        old_supervisor_user = User.objects.create_user(
            username='old-notification-supervisor',
            password='test-password',
        )
        old_supervisor = EmployeeProfile.objects.create(
            user=old_supervisor_user,
            role='Supervisor',
            department=department,
        )
        new_supervisor_user = User.objects.create_user(
            username='new-notification-supervisor',
            password='test-password',
        )
        new_supervisor = EmployeeProfile.objects.create(
            user=new_supervisor_user,
            role='Supervisor',
            department=department,
        )
        DepartmentSupervisor.objects.create(
            department=department,
            supervisor=old_supervisor,
        )
        self.client.force_login(hr_user)

        response = self.client.post(reverse('hr_dashboard'), {
            'assignment_action': 'save',
            'department': department.id,
            'supervisor': new_supervisor.id,
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#assignments")
        notified_recipient_ids = set(
            AppNotification.objects.values_list('recipient_id', flat=True),
        )
        self.assertEqual(
            notified_recipient_ids,
            {self.user.id, old_supervisor_user.id, new_supervisor_user.id},
        )


class HRSupervisorAssignmentTests(TestCase):
    def setUp(self):
        self.hr_user = User.objects.create_user(
            username='assignment-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=self.hr_user, role='HR')
        self.department = Department.objects.create(name='Human Resources')
        self.other_department = Department.objects.create(name='Finance')
        employee_user = User.objects.create_user(
            username='assigned-employee',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=employee_user,
            role='Employee',
            department=self.department,
        )
        old_supervisor_user = User.objects.create_user(
            username='old-supervisor',
            password='test-password',
        )
        self.old_supervisor = EmployeeProfile.objects.create(
            user=old_supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        new_supervisor_user = User.objects.create_user(
            username='new-supervisor',
            password='test-password',
        )
        self.new_supervisor = EmployeeProfile.objects.create(
            user=new_supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        subordinate_supervisor_user = User.objects.create_user(
            username='subordinate-supervisor',
            password='test-password',
        )
        self.subordinate_supervisor = EmployeeProfile.objects.create(
            user=subordinate_supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        foreign_supervisor_user = User.objects.create_user(
            username='foreign-supervisor',
            password='test-password',
        )
        self.foreign_supervisor = EmployeeProfile.objects.create(
            user=foreign_supervisor_user,
            role='Supervisor',
            department=self.other_department,
        )
        self.hod = EmployeeProfile.objects.create(
            user=User.objects.create_user(
                username='assignment-hod',
                password='test-password',
            ),
            role='HOD',
            department=self.department,
        )
        self.hr_user.profile.department = self.department
        self.hr_user.profile.save(update_fields=['department'])
        self.assignment = DepartmentSupervisor.objects.create(
            department=self.department,
            supervisor=self.old_supervisor,
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.draft_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=cycle,
            supervisor=self.old_supervisor,
        )
        self.client.force_login(self.hr_user)

    def test_options_endpoint_returns_department_users_except_hr(self):
        response = self.client.get(
            reverse('hr_assignment_options'),
            {'department_id': self.department.id},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            {supervisor['id'] for supervisor in data['supervisors']},
            {
                self.old_supervisor.id,
                self.new_supervisor.id,
                self.subordinate_supervisor.id,
                self.employee.id,
                self.hod.id,
            },
        )
        self.assertNotIn(
            self.foreign_supervisor.id,
            [supervisor['id'] for supervisor in data['supervisors']],
        )
        self.assertNotIn(
            self.hr_user.profile.id,
            [supervisor['id'] for supervisor in data['supervisors']],
        )

    def test_hr_dashboard_can_assign_department_hod(self):
        response = self.client.post(reverse('hr_dashboard'), {
            'hod_assignment_action': 'save',
            'department': self.department.id,
            'hod': self.hod.id,
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#assignments")
        self.assertEqual(
            AssignDepartmentHod.objects.get(department=self.department).hod,
            self.hod,
        )

    def test_hr_dashboard_can_assign_supervisor_reporting_line(self):
        response = self.client.post(reverse('hr_dashboard'), {
            'reporting_action': 'save',
            'reporting-supervisor': self.subordinate_supervisor.id,
            'reporting-manager': self.hod.id,
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#assignments")
        self.assertEqual(
            SupervisorReporting.objects.get(
                supervisor=self.subordinate_supervisor,
            ).manager,
            self.hod,
        )

    def test_hr_dashboard_confirms_transfer_before_changing_supervisor_manager(self):
        next_hod = EmployeeProfile.objects.create(
            user=User.objects.create_user(
                username='dashboard-next-hod',
                password='test-password',
            ),
            role='HOD',
            department=self.department,
        )
        SupervisorReporting.objects.create(
            supervisor=self.subordinate_supervisor,
            manager=self.hod,
        )
        appraisal = Appraisal.objects.create(
            employee=self.subordinate_supervisor,
            cycle=self.draft_appraisal.cycle,
            supervisor=self.hod,
            status='pending_manager',
        )
        assignment_data = {
            'reporting_action': 'save',
            'reporting-supervisor': self.subordinate_supervisor.id,
            'reporting-manager': next_hod.id,
        }

        response = self.client.post(reverse('hr_dashboard'), assignment_data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reporting_confirmation']['appraisal_count'], 1)
        self.assertContains(response, 'Confirm supervisor transfer')
        self.assertEqual(
            SupervisorReporting.objects.get(
                supervisor=self.subordinate_supervisor,
            ).manager,
            self.hod,
        )

        response = self.client.post(reverse('hr_dashboard'), {
            **assignment_data,
            'confirm_transfer': '1',
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#assignments")
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.supervisor, next_hod)
        self.assertEqual(
            SupervisorReporting.objects.get(
                supervisor=self.subordinate_supervisor,
            ).manager,
            next_hod,
        )

    def test_hr_dashboard_assignment_section_includes_mobile_table_scrolling(self):
        response = self.client.get(reverse('hr_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Assign or Change Department HOD')
        self.assertContains(response, 'Assign or Change a Supervisor’s HOD')
        self.assertContains(response, 'Current Supervisor Reporting Lines')
        self.assertContains(
            response,
            '.hr-sidebar-nav { display: grid; gap: .55rem; grid-template-columns: minmax(0, 1fr); }',
        )
        self.assertContains(response, 'transform: translateX(-105%)')
        self.assertContains(response, 'hr-sidebar-backdrop')
        self.assertContains(response, '.hr-sidebar-brand { display: none; }')
        self.assertContains(response, 'hr-sidebar-drawer-brand')
        self.assertContains(response, 'Mathias Catholic Hospital<small>HR WORKSPACE</small>')
        self.assertContains(response, 'data-sidebar-close')
        self.assertContains(response, '.hr-dashboard-content { padding: 4.75rem 1.1rem 1.1rem; }')
        self.assertContains(response, 'top: 4.5rem;')
        self.assertContains(response, "event.key === 'Escape'")
        self.assertContains(response, 'overflow-x: auto')
        self.assertContains(response, 'min-width: 720px')

    def test_hr_dashboard_mobile_navigation_is_vertical_and_collapsible(self):
        response = self.client.get(reverse('hr_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'grid-template-columns: minmax(0, 1fr)')
        self.assertContains(response, 'transform: translateX(-105%)')
        self.assertContains(response, 'hr-sidebar-backdrop')
        self.assertContains(response, "event.key === 'Escape'")
        self.assertContains(response, 'navigation.inert = mobile && !expanded')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, '.hr-sidebar-brand { display: none; }')
        self.assertContains(response, 'Mathias Catholic Hospital<small>HR WORKSPACE</small>')
        self.assertContains(response, 'data-sidebar-close')
        self.assertContains(response, 'top: 4.5rem;')

    def test_hr_can_assign_an_employee_as_department_reviewer(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'supervisor': self.employee.id,
        })

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.supervisor, self.employee)

    def test_search_endpoint_filters_staff_id_department_and_related_names(self):
        response = self.client.get(
            reverse('hr_search_supervisor_assignments'),
            {'q': 'Human Resources'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['results'][0]['department'], 'Human Resources')

        response = self.client.get(
            reverse('hr_search_supervisor_assignments'),
            {'department': self.other_department.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 0)

        response = self.client.get(reverse('hr_search_supervisor_assignments'), {'q': 'old-supervisor'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)

    def test_search_endpoint_paginates_and_is_hr_only(self):
        for index in range(26):
            department = Department.objects.create(name=f'Page Department {index:02}')
            user = User.objects.create_user(
                username=f'page-supervisor-{index:02}',
                password='test-password',
            )
            supervisor = EmployeeProfile.objects.create(
                user=user,
                role='Supervisor',
                department=department,
            )
            DepartmentSupervisor.objects.create(
                department=department,
                supervisor=supervisor,
            )

        response = self.client.get(reverse('hr_search_supervisor_assignments'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 27)
        self.assertEqual(len(response.json()['results']), 25)
        self.assertTrue(response.json()['has_next'])

        self.client.force_login(self.employee.user)
        response = self.client.get(reverse('hr_search_supervisor_assignments'))
        self.assertEqual(response.status_code, 403)

    def test_hr_can_change_assignment_and_update_draft_appraisal(self):
        coworker_user = User.objects.create_user(
            username='assigned-coworker',
            password='test-password',
        )
        coworker = EmployeeProfile.objects.create(
            user=coworker_user,
            role='Employee',
            department=self.department,
        )
        coworker_appraisal = Appraisal.objects.create(
            employee=coworker,
            cycle=self.draft_appraisal.cycle,
            status='draft',
        )
        active_appraisal = Appraisal.objects.create(
            employee=self.subordinate_supervisor,
            cycle=self.draft_appraisal.cycle,
            supervisor=self.old_supervisor,
            status='pending_manager',
        )
        returned_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=AppraisalCycle.objects.create(year=2027),
            supervisor=self.old_supervisor,
            status='returned_employee',
        )
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'supervisor': self.new_supervisor.id,
        })

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.supervisor, self.new_supervisor)
        self.draft_appraisal.refresh_from_db()
        self.assertEqual(self.draft_appraisal.supervisor, self.new_supervisor)
        coworker_appraisal.refresh_from_db()
        self.assertEqual(coworker_appraisal.supervisor, self.new_supervisor)
        active_appraisal.refresh_from_db()
        self.assertEqual(active_appraisal.supervisor, self.old_supervisor)
        returned_appraisal.refresh_from_db()
        self.assertEqual(returned_appraisal.supervisor, self.old_supervisor)

    def test_dashboard_quick_access_can_update_supervisor_assignment(self):
        response = self.client.get(reverse('hr_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-dashboard-target="assignments"')
        self.assertContains(response, 'data-dashboard-section="assignments"')

        response = self.client.post(reverse('hr_dashboard'), {
            'assignment_action': 'save',
            'department': self.department.id,
            'supervisor': self.new_supervisor.id,
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#assignments")
        self.assertEqual(
            DepartmentSupervisor.objects.get(department=self.department).supervisor,
            self.new_supervisor,
        )
        self.draft_appraisal.refresh_from_db()
        self.assertEqual(self.draft_appraisal.supervisor, self.new_supervisor)

    def test_dashboard_assignment_requires_confirmation_for_in_progress_transfer(self):
        active_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=self.draft_appraisal.cycle,
            supervisor=self.old_supervisor,
            status='pending_manager',
        )
        response = self.client.post(reverse('hr_dashboard'), {
            'assignment_action': 'save',
            'department': self.department.id,
            'supervisor': self.new_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirm appraisal transfer')
        self.assertContains(response, 'data-has-errors="true"')
        self.assertEqual(
            DepartmentSupervisor.objects.get(department=self.department).supervisor,
            self.old_supervisor,
        )
        active_appraisal.refresh_from_db()
        self.assertEqual(active_appraisal.supervisor, self.old_supervisor)

    def test_hr_confirms_transfer_of_in_progress_appraisals_and_preserves_history(self):
        pending_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=self.draft_appraisal.cycle,
            supervisor=self.old_supervisor,
            status='pending_manager',
            supervisor_comments='Work is progressing well.',
        )
        returned_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=AppraisalCycle.objects.create(year=2027),
            supervisor=self.old_supervisor,
            status='returned_supervisor',
            supervisor_comments='Please add further detail.',
        )
        hod_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=AppraisalCycle.objects.create(year=2028),
            supervisor=self.old_supervisor,
            status='pending_hod',
        )
        return_record = AppraisalReturn.objects.create(
            appraisal=returned_appraisal,
            reviewer=self.old_supervisor.user,
            reviewer_name='Old Supervisor',
            stage='supervisor',
            from_status='pending_manager',
            to_status='returned_employee',
            comment='Please add further detail.',
        )

        assignment_data = {
            'department': self.department.id,
            'supervisor': self.new_supervisor.id,
        }
        response = self.client.post(reverse('hr_supervisor_assignments'), assignment_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirm appraisal transfer')
        self.assertContains(response, '2 in-progress')
        self.assertEqual(
            DepartmentSupervisor.objects.get(department=self.department).supervisor,
            self.old_supervisor,
        )
        pending_appraisal.refresh_from_db()
        self.assertEqual(pending_appraisal.supervisor, self.old_supervisor)

        response = self.client.post(
            reverse('hr_supervisor_assignments'),
            {**assignment_data, 'confirm_transfer': '1'},
        )

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        self.assertEqual(
            DepartmentSupervisor.objects.get(department=self.department).supervisor,
            self.new_supervisor,
        )
        pending_appraisal.refresh_from_db()
        returned_appraisal.refresh_from_db()
        hod_appraisal.refresh_from_db()
        return_record.refresh_from_db()
        self.assertEqual(pending_appraisal.supervisor, self.new_supervisor)
        self.assertEqual(pending_appraisal.status, 'pending_manager')
        self.assertEqual(pending_appraisal.supervisor_comments, 'Work is progressing well.')
        self.assertEqual(returned_appraisal.supervisor, self.new_supervisor)
        self.assertEqual(returned_appraisal.status, 'returned_supervisor')
        self.assertEqual(
            returned_appraisal.supervisor_comments,
            'Please add further detail.',
        )
        self.assertEqual(returned_appraisal.returns.get(), return_record)
        self.assertEqual(hod_appraisal.supervisor, self.old_supervisor)

        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.old_supervisor.id}': 'Employee',
        })
        self.assertRedirects(response, f"{reverse('hr_dashboard')}#roles")
        self.old_supervisor.refresh_from_db()
        self.assertEqual(self.old_supervisor.role, 'Employee')

    def test_hr_can_assign_an_hod_to_supervise_a_supervisor(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'reporting_action': 'save',
            'reporting-supervisor': self.subordinate_supervisor.id,
            'reporting-manager': self.hod.id,
        })

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        assignment = SupervisorReporting.objects.get(supervisor=self.subordinate_supervisor)
        self.assertEqual(assignment.manager, self.hod)

    def test_hr_confirms_supervisor_reporting_transfer_and_preserves_returned_reviewers(self):
        new_hod = EmployeeProfile.objects.create(
            user=User.objects.create_user(
                username='replacement-hod',
                password='test-password',
            ),
            role='HOD',
            department=self.department,
        )
        SupervisorReporting.objects.create(
            supervisor=self.subordinate_supervisor,
            manager=self.hod,
        )
        draft_appraisal = Appraisal.objects.create(
            employee=self.subordinate_supervisor,
            cycle=AppraisalCycle.objects.create(year=2027),
            supervisor=self.hod,
            status='draft',
        )
        active_appraisal = Appraisal.objects.create(
            employee=self.subordinate_supervisor,
            cycle=AppraisalCycle.objects.create(year=2028),
            supervisor=self.hod,
            status='pending_manager',
        )
        returned_appraisal = Appraisal.objects.create(
            employee=self.subordinate_supervisor,
            cycle=AppraisalCycle.objects.create(year=2029),
            supervisor=self.hod,
            status='returned_employee',
        )
        assignment_data = {
            'reporting_action': 'save',
            'reporting-supervisor': self.subordinate_supervisor.id,
            'reporting-manager': new_hod.id,
        }

        response = self.client.post(
            reverse('hr_supervisor_assignments'),
            assignment_data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['transfer_confirmation']['appraisal_count'],
            1,
        )
        active_appraisal.refresh_from_db()
        self.assertEqual(active_appraisal.supervisor, self.hod)

        response = self.client.post(
            reverse('hr_supervisor_assignments'),
            {**assignment_data, 'confirm_transfer': '1'},
        )

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        self.assertEqual(
            SupervisorReporting.objects.get(
                supervisor=self.subordinate_supervisor,
            ).manager,
            new_hod,
        )
        draft_appraisal.refresh_from_db()
        active_appraisal.refresh_from_db()
        returned_appraisal.refresh_from_db()
        self.assertEqual(draft_appraisal.supervisor, new_hod)
        self.assertEqual(active_appraisal.supervisor, new_hod)
        self.assertEqual(returned_appraisal.supervisor, self.hod)

    def test_hr_cannot_assign_a_supervisor_to_themselves(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'reporting_action': 'save',
            'reporting-supervisor': self.subordinate_supervisor.id,
            'reporting-manager': self.subordinate_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice.')
        self.assertFalse(
            SupervisorReporting.objects.filter(supervisor=self.subordinate_supervisor).exists()
        )

    def test_hr_cannot_assign_a_supervisor_as_another_supervisors_manager(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'reporting_action': 'save',
            'reporting-supervisor': self.new_supervisor.id,
            'reporting-manager': self.subordinate_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice.')
        self.assertFalse(
            SupervisorReporting.objects.filter(supervisor=self.new_supervisor).exists()
        )

    def test_hr_cannot_assign_supervisor_from_another_department(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'supervisor': self.foreign_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose a department reviewer from the selected department.')
        self.assertEqual(
            DepartmentSupervisor.objects.get(department=self.department).supervisor,
            self.old_supervisor,
        )

    def test_non_hr_user_cannot_manage_assignments(self):
        employee_user = self.employee.user
        self.client.force_login(employee_user)

        response = self.client.get(reverse('hr_supervisor_assignments'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard_router'))


class HRCompletedAppraisalTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Technology')
        self.other_department = Department.objects.create(name='Finance')
        self.hr_user = User.objects.create_user(
            username='completed-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(user=self.hr_user, role='HR')
        employee_user = User.objects.create_user(
            username='completed-staff',
            first_name='Alex',
            last_name='Morgan',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=employee_user,
            role='Employee',
            department=self.department,
        )
        supervisor_user = User.objects.create_user(
            username='completed-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        hod_user = User.objects.create_user(
            username='completed-hod',
            password='test-password',
        )
        self.hod = EmployeeProfile.objects.create(
            user=hod_user,
            role='HOD',
            department=self.department,
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.completed = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            hod=self.hod,
            cycle=cycle,
            employee_department=self.department,
            employee_department_name=self.department.name,
            status='completed',
            overall_percentage='85.00',
            final_rating='Outstanding',
            hr_comments='Approved.',
        )
        completed_competency = Competency.objects.create(
            name='Service quality',
            competency_type='core',
            description='Deliver consistent, high-quality service.',
        )
        self.completed.competency_scores.create(competency=completed_competency)
        self.completed.trainings.create(
            institution='Civil Service Training Centre',
            date='2026-04-12',
            programme='Leadership Development',
        )
        self.pending = Appraisal.objects.create(
            employee=EmployeeProfile.objects.create(
                user=User.objects.create_user(
                    username='pending-staff',
                    password='test-password',
                ),
                role='Employee',
                department=self.other_department,
            ),
            cycle=cycle,
            status='pending_hr',
        )
        previous_cycle = AppraisalCycle.objects.create(year=2025)
        self.previous_department_appraisal = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            hod=self.hod,
            cycle=previous_cycle,
            employee_department=self.other_department,
            employee_department_name=self.other_department.name,
            status='completed',
        )
        self.employee.department = self.other_department
        self.employee.save(update_fields=['department'])
        self.client.force_login(self.hr_user)

    def test_hr_dashboard_search_filters_completed_appraisals(self):
        response = self.client.get(reverse('hr_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'body:has(.role-dashboard-shell, .hr-dashboard-shell) > .navbar-appraisal',
        )
        self.assertContains(response, 'Completed Appraisals')
        self.assertContains(response, 'Cycle year')
        self.assertContains(response, 'Appraisal analytics')
        self.assertContains(response, 'Workflow status')
        self.assertContains(response, 'Completion by department')
        self.assertContains(response, 'data-dashboard-target="overview"')
        self.assertContains(response, 'data-dashboard-target="pending"')
        self.assertContains(response, 'data-dashboard-target="completed"')
        self.assertContains(response, 'data-dashboard-target="roles"')
        self.assertContains(response, 'data-dashboard-target="assignments"')
        self.assertContains(response, 'Supervisor assignments')
        self.assertContains(response, 'id="hr-dashboard-assignment-form"')
        self.assertContains(response, f'name="role_{self.employee.id}"')
        self.assertContains(response, '>Save</button>')
        self.assertEqual(response.context['dashboard_counts']['total'], 2)
        self.assertEqual(response.context['dashboard_counts']['completed'], 1)
        self.assertEqual(response.context['dashboard_counts']['hr'], 1)
        self.assertEqual(
            response.context['dashboard_filters']['cycle'],
            2026,
        )

    def test_hr_dashboard_analytics_filters_cycle_and_department(self):
        response = self.client.get(reverse('hr_dashboard'), {
            'cycle': '2025',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_counts']['total'], 1)
        self.assertEqual(response.context['dashboard_counts']['completed'], 1)
        self.assertEqual(response.context['dashboard_filters']['cycle'], 2025)

        response = self.client.get(reverse('hr_dashboard'), {
            'cycle': '2026',
            'department': str(self.department.id),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_counts']['total'], 1)
        self.assertEqual(response.context['dashboard_counts']['completed'], 1)
        self.assertEqual(
            response.context['department_chart'],
            [{'name': 'Technology', 'completed': 1, 'in_progress': 0}],
        )

    def test_hr_can_change_user_role_and_group_is_updated(self):
        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.employee.id}': 'Supervisor',
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#roles")
        self.employee.refresh_from_db()
        self.employee.user.refresh_from_db()
        self.assertEqual(self.employee.role, 'Supervisor')
        self.assertEqual(
            set(self.employee.user.groups.values_list('name', flat=True)),
            {'Supervisor'},
        )

    def test_hr_cannot_change_supervisor_role_before_reassigning_workload(self):
        assignment = DepartmentSupervisor.objects.create(
            department=self.department,
            supervisor=self.supervisor,
        )
        active_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=self.completed.cycle,
            supervisor=self.supervisor,
            status='pending_manager',
        )

        response = self.client.post(
            reverse('hr_dashboard'),
            {f'role_{self.supervisor.id}': 'Employee'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.supervisor.refresh_from_db()
        active_appraisal.refresh_from_db()
        self.assertEqual(self.supervisor.role, 'Supervisor')
        self.assertEqual(active_appraisal.supervisor, self.supervisor)
        self.assertEqual(assignment.supervisor, self.supervisor)
        self.assertContains(response, 'Reassign the following supervisor workload')
        self.assertContains(response, '1 appraisal(s) awaiting review')
        self.assertContains(response, 'department or supervisor reporting assignments')

    def test_hr_cannot_assign_hr_role_or_update_an_hr_profile(self):
        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.employee.id}': 'HR',
        })

        self.assertRedirects(response, f"{reverse('hr_dashboard')}#roles")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.role, 'Employee')

        response = self.client.get(
            reverse('hr_search_completed_appraisals'),
            {'q': 'completed-staff'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total'], 2)
        self.assertEqual(
            {result['id'] for result in data['results']},
            {self.completed.id, self.previous_department_appraisal.id},
        )
        departments_by_id = {
            result['id']: result['department']
            for result in data['results']
        }
        self.assertEqual(departments_by_id[self.completed.id], 'Technology')
        self.assertEqual(departments_by_id[self.previous_department_appraisal.id], 'Finance')

        response = self.client.get(
            reverse('hr_search_completed_appraisals'),
            {'department': self.department.id, 'q': 'completed-staff'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['results'][0]['id'], self.completed.id)

        response = self.client.get(
            reverse('hr_search_completed_appraisals'),
            {'department': self.other_department.id, 'q': 'completed-staff'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(
            response.json()['results'][0]['id'],
            self.previous_department_appraisal.id,
        )

        self.assertEqual(data['results'][0]['final_rating'], 'Outstanding')
        self.assertEqual(data['results'][0]['cycle_year'], 2026)

        response = self.client.get(
            reverse('hr_search_completed_appraisals'),
            {'department': self.department.id, 'cycle_year': '2026'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)

        response = self.client.get(
            reverse('hr_search_completed_appraisals'),
            {'department': self.other_department.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(
            response.json()['results'][0]['id'],
            self.previous_department_appraisal.id,
        )

    def test_hr_can_view_completed_appraisal_but_not_pending_detail(self):
        response = self.client.get(
            reverse('hr_completed_appraisal', args=[self.completed.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approved.')
        self.assertContains(response, 'completed and locked')
        self.assertContains(response, 'Deliver consistent, high-quality service.')
        self.assertContains(response, 'Leadership Development')
        self.assertContains(response, 'Technology')

        response = self.client.get(
            reverse('hr_completed_appraisal', args=[self.pending.id]),
        )
        self.assertEqual(response.status_code, 404)

    def test_completed_appraisal_admin_is_locked_for_hr_but_editable_by_superuser(self):
        model_admin = admin.site._registry[Appraisal]
        target_inline = next(
            inline for inline in model_admin.inlines
            if inline.model is AppraisalTarget
        )(Appraisal, admin.site)
        competency_inline = next(
            inline for inline in model_admin.inlines
            if inline.model is AppraisalCompetencyScore
        )(Appraisal, admin.site)
        training_inline = next(
            inline for inline in model_admin.inlines
            if inline.model is AppraisalTraining
        )(Appraisal, admin.site)
        request_factory = RequestFactory()
        hr_request = request_factory.get('/admin/app/appraisal/')
        hr_request.user = self.hr_user
        superuser = User.objects.create_superuser(
            username='appraisal-admin',
            email='admin@example.com',
            password='test-password',
        )
        admin_request = request_factory.get('/admin/app/appraisal/')
        admin_request.user = superuser

        self.assertIn('status', model_admin.get_readonly_fields(hr_request, self.completed))
        self.assertTrue(target_inline.has_delete_permission(hr_request, self.completed) is False)
        self.assertTrue(target_inline.has_add_permission(hr_request, self.completed) is False)
        self.assertTrue(competency_inline.has_delete_permission(hr_request, self.completed) is False)
        self.assertTrue(training_inline.has_delete_permission(hr_request, self.completed) is False)
        self.assertTrue(training_inline.has_add_permission(hr_request, self.completed) is False)
        self.assertEqual(model_admin.get_readonly_fields(admin_request, self.completed), ())
        self.assertEqual(target_inline.get_readonly_fields(admin_request, self.completed), ())
        self.assertEqual(training_inline.get_readonly_fields(admin_request, self.completed), ())
        self.assertTrue(target_inline.has_add_permission(admin_request, self.completed))
        self.assertTrue(target_inline.has_delete_permission(admin_request, self.completed))
        self.assertTrue(training_inline.has_add_permission(admin_request, self.completed))
        self.assertTrue(training_inline.has_delete_permission(admin_request, self.completed))

    def test_non_hr_user_cannot_search_completed_appraisals(self):
        self.client.force_login(self.employee.user)

        response = self.client.get(reverse('hr_search_completed_appraisals'))

        self.assertEqual(response.status_code, 403)


class EmployeeCompetencySelectionTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Finance')
        self.employee_user = User.objects.create_user(
            username='competency-employee',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            role='Employee',
            department=self.department,
        )
        supervisor_user = User.objects.create_user(
            username='competency-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        self.cycle = AppraisalCycle.objects.create(
            year=2026,
            is_active=True,
            report_period_from='2026-01-01',
            report_period_to='2026-12-31',
        )
        self.appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=self.cycle,
        )
        self.competencies = [
            Competency.objects.create(
                name='Communication',
                competency_type='core',
                description=(
                    'Communicate clearly with colleagues and customers.\n'
                    'Listen actively and respond constructively.'
                ),
            ),
            Competency.objects.create(
                name='Planning',
                competency_type='non_core',
                description='Organize work and manage workload systematically.',
            ),
        ]
        DepartmentSupervisor.objects.create(
            department=self.department,
            supervisor=self.supervisor,
        )
        self.client.force_login(self.employee_user)

    def test_selection_page_shows_competency_descriptions(self):
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Communicate clearly with colleagues and customers.')
        self.assertContains(response, 'Listen actively and respond constructively.')
        self.assertContains(response, 'Organize work and manage workload systematically.')
        self.assertContains(response, 'value="1:0"')
        self.assertContains(response, 'value="1:1"')
        self.assertContains(response, 'Core Competencies')
        self.assertContains(response, 'Non-Core Competencies')
        self.assertContains(response, 'data-step-percent')
        self.assertContains(response, 'data-step-state')
        self.assertEqual(response.content.decode().count('<h3>Communication</h3>'), 1)
        self.assertContains(response, '01/01/2026')
        self.assertContains(response, '31/12/2026')

    def test_competency_selection_uses_admin_display_order(self):
        planning = Competency.objects.create(
            name='Planning first',
            competency_type='core',
            display_order=1,
            description='Plan work.',
        )
        self.competencies[0].display_order = 2
        self.competencies[0].save(update_fields=['display_order'])

        form = EmployeeCompetencySelectionForm()
        core_names = [
            group['competency'].name
            for group in form.competency_groups
            if group['competency'].competency_type == 'core'
        ]

        self.assertLess(
            core_names.index(planning.name),
            core_names.index(self.competencies[0].name),
        )

    def competency_selection_data(self, competency_ids):
        return {
            'targets-TOTAL_FORMS': '5',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '5',
            'trainings-TOTAL_FORMS': '4',
            'trainings-INITIAL_FORMS': '0',
            'trainings-MIN_NUM_FORMS': '0',
            'trainings-MAX_NUM_FORMS': '4',
            'competencies': [str(competency_id) for competency_id in competency_ids],
        }

    def test_employee_can_select_competencies_for_supervisor_assessment(self):
        response = self.client.post(
            reverse('fill_targets'),
            self.competency_selection_data([self.competencies[0].id]),
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.assertQuerySetEqual(
            self.appraisal.competency_scores.order_by('competency_id').values_list(
                'competency_id',
                flat=True,
            ),
            [self.competencies[0].id],
        )

        self.appraisal.status = 'pending_manager'
        self.appraisal.supervisor = self.supervisor
        self.appraisal.save(update_fields=['status', 'supervisor'])
        self.client.force_login(self.supervisor.user)
        response = self.client.get(reverse('supervisor_review', args=[self.appraisal.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Communication')
        self.assertContains(response, 'Communicate clearly with colleagues and customers.')
        self.assertNotContains(response, 'Organize work and manage workload systematically.')

    def test_employee_must_select_a_competency_before_submission(self):
        response = self.client.post(
            reverse('submit_to_supervisor', args=[self.appraisal.id]),
        )

        self.assertRedirects(
            response,
            reverse('fill_targets_year', args=[self.appraisal.cycle.year]),
        )
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'draft')

    def test_save_and_submit_persists_selected_competencies_and_submits_appraisal(self):
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'action': 'submit',
            'employee_info-employee_comments': 'I achieved my service goals.',
            'targets-0-key_result_area': 'Improve service response',
            'targets-0-target_description': 'Reduce response time for patients.',
        })
        response = self.client.post(
            reverse('fill_targets'),
            data,
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_manager')
        self.assertEqual(self.appraisal.supervisor, self.supervisor)
        self.assertTrue(self.appraisal.competency_scores.filter(
            competency=self.competencies[0],
        ).exists())
        supervisor_notification = AppNotification.objects.get(
            recipient=self.supervisor.user,
        )
        self.assertEqual(
            supervisor_notification.title,
            'Appraisal awaiting your review',
        )
        self.assertEqual(
            supervisor_notification.url,
            reverse('supervisor_review', args=[self.appraisal.id]),
        )

    def test_submit_with_no_complete_target_returns_to_performance_planning_step(self):
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'action': 'submit',
            'employee_info-employee_comments': 'I achieved my service goals.',
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_step'], 1)
        self.assertContains(response, 'Add at least one key result area and its target before submitting.')

    def test_submit_without_employee_comments_returns_to_comments_step(self):
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'action': 'submit',
            'targets-0-key_result_area': 'Improve service response',
            'targets-0-target_description': 'Reduce response time for patients.',
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_step'], 3)
        self.assertContains(response, 'Add your overall assessment comments before submitting.')

    def test_employee_details_and_training_are_saved_as_appraisal_snapshot(self):
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'employee_info-employee_title': 'mr',
            'employee_info-employee_first_name': 'Alex',
            'employee_info-employee_last_name': 'Morgan',
            'employee_info-employee_other_names': 'Kwame',
            'employee_info-employee_gender': 'male',
            'employee_info-employee_job_title': 'Senior Analyst',
            'employee_info-employee_department': str(self.department.id),
            'employee_info-employee_appointment_to_present_grade_date': '2021-02-10',
            'trainings-0-institution': 'Public Service Institute',
            'trainings-0-date': '2026-05-20',
            'trainings-0-programme': 'Records Management',
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.employee_title, 'mr')
        self.assertEqual(self.appraisal.employee_first_name, 'Alex')
        self.assertEqual(self.appraisal.employee_last_name, 'Morgan')
        self.assertEqual(self.appraisal.employee_other_names, 'Kwame')
        self.assertEqual(self.appraisal.employee_department, self.department)
        self.assertEqual(self.appraisal.employee_department_name, 'Finance')
        training = self.appraisal.trainings.get()
        self.assertEqual(training.institution, 'Public Service Institute')
        self.assertEqual(str(training.date), '2026-05-20')
        self.assertEqual(training.programme, 'Records Management')

        self.appraisal.status = 'pending_manager'
        self.appraisal.save(update_fields=['status'])
        response = self.client.get(reverse('fill_targets'))
        self.assertContains(response, 'Public Service Institute')
        self.assertContains(response, 'Senior Analyst')

    def test_existing_employee_details_are_read_only_and_posted_changes_are_ignored(self):
        self.employee_user.first_name = 'Current'
        self.employee_user.last_name = 'Employee'
        self.employee_user.save(update_fields=['first_name', 'last_name'])
        self.employee.job_title = 'Current Analyst'
        self.employee.save(update_fields=['job_title'])

        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        employee_info_form = response.context['employee_info_form']
        self.assertTrue(employee_info_form.fields['employee_first_name'].disabled)
        self.assertTrue(employee_info_form.fields['employee_last_name'].disabled)
        self.assertTrue(employee_info_form.fields['employee_job_title'].disabled)
        self.assertTrue(employee_info_form.fields['employee_department'].disabled)

        other_department = Department.objects.create(name='Human Resources')
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'employee_info-employee_first_name': 'Changed',
            'employee_info-employee_last_name': 'Name',
            'employee_info-employee_job_title': 'Changed Position',
            'employee_info-employee_department': str(other_department.id),
        })
        response = self.client.post(reverse('fill_targets'), data)

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.employee_first_name, 'Current')
        self.assertEqual(self.appraisal.employee_last_name, 'Employee')
        self.assertEqual(self.appraisal.employee_job_title, 'Current Analyst')
        self.assertEqual(self.appraisal.employee_department, self.department)

    def test_employee_can_update_selection_only_while_appraisal_is_draft(self):
        self.appraisal.competency_scores.create(competency=self.competencies[0])

        response = self.client.post(
            reverse('fill_targets'),
            self.competency_selection_data([self.competencies[1].id]),
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.assertFalse(self.appraisal.competency_scores.filter(
            competency=self.competencies[0],
        ).exists())
        self.assertTrue(self.appraisal.competency_scores.filter(
            competency=self.competencies[1],
        ).exists())

        self.appraisal.status = 'pending_manager'
        self.appraisal.save(update_fields=['status'])
        response = self.client.get(reverse('fill_targets'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Planning')
        self.assertNotContains(response, 'Communicate clearly with colleagues and customers.')
        self.assertNotContains(response, 'Select Competencies')

    def test_employee_can_select_individual_statements(self):
        selected_statement = self.competencies[0].description_items[0]
        response = self.client.post(
            reverse('fill_targets'),
            {
                **self.competency_selection_data([]),
                'competency_statements': [f'{self.competencies[0].id}:0'],
            },
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        competency_score = self.appraisal.competency_scores.get(
            competency=self.competencies[0],
        )
        self.assertEqual(
            list(competency_score.statement_scores.values_list('statement', flat=True)),
            [selected_statement],
        )
        self.assertFalse(
            self.appraisal.competency_scores.filter(
                competency=self.competencies[1],
            ).exists()
        )

    def test_saving_targets_from_their_step_persists_and_continues(self):
        data = self.competency_selection_data([])
        data.update({
            'action': 'save_targets',
            'targets-0-key_result_area': 'Improve service response',
            'targets-0-target_description': 'Reduce response time for patients.',
            'targets-0-resources_required': 'Staff training',
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertRedirects(
            response,
            f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=2",
        )
        target = self.appraisal.targets.get()
        self.assertEqual(target.key_result_area, 'Improve service response')
        self.assertEqual(target.target_description, 'Reduce response time for patients.')

    def test_saving_competencies_and_comments_from_their_step_persists(self):
        data = self.competency_selection_data([self.competencies[0].id])
        data.update({
            'action': 'save_competencies',
            'competency_statements': [f'{self.competencies[0].id}:0'],
            'employee_info-employee_comments': 'I achieved my service goals.',
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertRedirects(
            response,
            f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=4",
        )
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.employee_comments, 'I achieved my service goals.')
        competency_score = self.appraisal.competency_scores.get(
            competency=self.competencies[0],
        )
        self.assertEqual(
            list(competency_score.statement_scores.values_list('statement', flat=True)),
            [self.competencies[0].description_items[0]],
        )

    def test_saving_core_competencies_advances_to_non_core_step(self):
        data = self.competency_selection_data([])
        data.update({
            'action': 'save_core_competencies',
            'competency_statements': [
                f'{self.competencies[0].id}:0',
                f'{self.competencies[1].id}:0',
            ],
        })

        response = self.client.post(reverse('fill_targets'), data)

        self.assertRedirects(
            response,
            f"{reverse('fill_targets_year', args=[self.appraisal.cycle.year])}?step=3",
        )
        self.assertTrue(
            self.appraisal.competency_scores.filter(
                competency=self.competencies[0],
            ).exists()
        )
        self.assertTrue(
            self.appraisal.competency_scores.filter(
                competency=self.competencies[1],
            ).exists()
        )

    def test_invalid_statement_choice_preserves_only_submitted_selections(self):
        response = self.client.post(
            reverse('fill_targets'),
            {
                **self.competency_selection_data([]),
                'competency_statements': ['not-a-valid-statement'],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice.')
        self.assertNotContains(response, 'value="1:0" id="competency-statement-1-0" checked')
        self.assertFalse(self.appraisal.competency_scores.exists())


class SupervisorDashboardOwnAppraisalVisibilityTests(TestCase):
    def setUp(self):
        self.cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.department = Department.objects.create(name='Operations')
        supervisor_user = User.objects.create_user(
            username='dashboard-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        manager_user = User.objects.create_user(
            username='dashboard-manager',
            password='test-password',
        )
        self.manager = EmployeeProfile.objects.create(
            user=manager_user,
            role='HOD',
            department=self.department,
        )
        self.client.force_login(supervisor_user)

    def test_own_appraisal_is_hidden_without_an_assigned_supervisor(self):
        response = self.client.get(reverse('supervisor_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'My Own Appraisal')
        self.assertFalse(Appraisal.objects.filter(
            employee=self.supervisor,
            cycle=self.cycle,
        ).exists())

    def test_own_appraisal_is_shown_when_a_supervisor_is_assigned(self):
        SupervisorReporting.objects.create(
            supervisor=self.supervisor,
            manager=self.manager,
        )

        response = self.client.get(reverse('supervisor_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Own Appraisal')
        self.assertEqual(response.context['my_appraisal'].employee, self.supervisor)

    def test_previous_cycle_own_appraisal_can_be_opened_and_edited(self):
        SupervisorReporting.objects.create(
            supervisor=self.supervisor,
            manager=self.manager,
        )
        previous_cycle = AppraisalCycle.objects.create(year=2025, is_active=False)

        response = self.client.get(reverse('supervisor_dashboard'))

        self.assertEqual(response.status_code, 200)
        previous_appraisal = Appraisal.objects.get(
            employee=self.supervisor,
            cycle=previous_cycle,
        )
        self.assertIn(previous_appraisal, response.context['my_appraisals'])
        self.assertContains(
            response,
            reverse('fill_targets_year', args=[previous_cycle.year]),
        )

        form_response = self.client.get(
            reverse('fill_targets_year', args=[previous_cycle.year]),
        )
        self.assertEqual(form_response.status_code, 200)
        self.assertFalse(form_response.context['read_only'])


class ReviewerAppraisalDashboardNavigationTests(TestCase):
    def setUp(self):
        self.cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        department = Department.objects.create(name='Operations')
        employee_user = User.objects.create_user(
            username='navigation-employee',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=employee_user,
            role='Employee',
            department=department,
        )
        supervisor_user = User.objects.create_user(
            username='navigation-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=department,
        )
        hod_user = User.objects.create_user(
            username='navigation-hod',
            password='test-password',
        )
        self.hod = EmployeeProfile.objects.create(
            user=hod_user,
            role='HOD',
            department=department,
        )

    def test_supervisor_appraisal_view_links_back_to_supervisor_dashboard(self):
        appraisal = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            cycle=self.cycle,
            status='pending_manager',
        )
        self.client.force_login(self.supervisor.user)

        response = self.client.get(
            reverse('supervisor_view_appraisal', args=[appraisal.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("supervisor_dashboard")}"',
        )

    def test_supervisor_own_goal_form_links_back_to_supervisor_dashboard(self):
        self.client.force_login(self.supervisor.user)

        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("supervisor_dashboard")}"',
        )

    def test_hod_appraisal_view_links_back_to_hod_dashboard(self):
        appraisal = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            hod=self.hod,
            cycle=self.cycle,
            status='pending_hod',
        )
        self.client.force_login(self.hod.user)

        response = self.client.get(
            reverse('hod_view_appraisal', args=[appraisal.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("hod_dashboard")}"',
        )

    def test_hod_can_open_and_edit_own_previous_cycle_appraisal(self):
        previous_cycle = AppraisalCycle.objects.create(year=2025, is_active=False)
        self.client.force_login(self.hod.user)

        response = self.client.get(reverse('hod_dashboard'))

        self.assertEqual(response.status_code, 200)
        previous_appraisal = Appraisal.objects.get(
            employee=self.hod,
            cycle=previous_cycle,
        )
        self.assertIn(previous_appraisal, response.context['my_appraisals'])
        self.assertContains(
            response,
            reverse('fill_targets_year', args=[previous_cycle.year]),
        )

        form_response = self.client.get(
            reverse('fill_targets_year', args=[previous_cycle.year]),
        )
        self.assertEqual(form_response.status_code, 200)
        self.assertFalse(form_response.context['read_only'])


class SupervisorCreateAppraisalTests(TestCase):
    def setUp(self):
        self.cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        department = Department.objects.create(name='Operations')
        supervisor_user = User.objects.create_user(
            username='supervisor-001',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=department,
        )
        self.employees = []
        for staff_id in ('employee-001', 'employee-002'):
            user = User.objects.create_user(username=staff_id, password='test-password')
            employee = EmployeeProfile.objects.create(
                user=user,
                role='Employee',
                department=department,
            )
            self.employees.append(employee)
        DepartmentSupervisor.objects.create(
            department=department,
            supervisor=self.supervisor,
        )
        self.client.force_login(supervisor_user)

    def test_supervisor_can_create_for_one_employee_then_all_assigned_employees(self):
        url = reverse('supervisor_create_appraisals')

        response = self.client.post(url, {
            'employee_selection': str(self.employees[0].id),
        })
        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.assertTrue(Appraisal.objects.filter(
            employee=self.employees[0],
            cycle=self.cycle,
            supervisor=self.supervisor,
            status='draft',
        ).exists())
        self.assertFalse(Appraisal.objects.filter(employee=self.employees[1]).exists())

        response = self.client.post(url, {'employee_selection': 'all'})
        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.assertEqual(
            Appraisal.objects.filter(cycle=self.cycle, supervisor=self.supervisor).count(),
            2,
        )

    def test_supervisor_cannot_create_appraisal_for_unassigned_employee(self):
        other_user = User.objects.create_user(username='unassigned-001', password='test-password')
        unassigned = EmployeeProfile.objects.create(user=other_user, role='Employee')

        response = self.client.post(reverse('supervisor_create_appraisals'), {
            'employee_selection': str(unassigned.id),
        })

        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.assertFalse(Appraisal.objects.filter(employee=unassigned).exists())


class SupervisorReviewScoreVisibilityTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name='Customer Service')
        supervisor_user = User.objects.create_user(
            username='review-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=department,
        )
        employee_user = User.objects.create_user(
            username='review-employee',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=employee_user,
            role='Employee',
            department=department,
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            cycle=cycle,
            status='pending_manager',
        )
        self.target = AppraisalTarget.objects.create(
            appraisal=self.appraisal,
            key_result_area='Resolve requests',
            target_description='Resolve requests on time.',
        )
        competency = Competency.objects.create(
            name='Communication',
            competency_type='core',
            description='Share information clearly and listen actively.',
        )
        self.competency_score = self.appraisal.competency_scores.create(
            competency=competency,
        )
        self.statement_score = self.competency_score.statement_scores.create(
            statement_index=0,
            statement=competency.description_items[0],
        )
        self.client.force_login(supervisor_user)

    def review_data(self, target_score='4'):
        return {
            'targets-TOTAL_FORMS': '1',
            'targets-INITIAL_FORMS': '1',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '1000',
            'targets-0-id': str(self.target.id),
            'targets-0-mid_year_progress': 'achieved',
            'targets-0-mid_year_remark': '',
            'targets-0-end_year_assessment': '',
            'targets-0-end_year_weight': '1',
            'targets-0-end_year_score': target_score,
            'targets-0-end_year_comment': '',
            'competency_scores-TOTAL_FORMS': '1',
            'competency_scores-INITIAL_FORMS': '1',
            'competency_scores-MIN_NUM_FORMS': '0',
            'competency_scores-MAX_NUM_FORMS': '1000',
            'competency_scores-0-id': str(self.competency_score.id),
            'competency_scores-0-mid_year_progress': 'well_exhibited',
            'competency_scores-0-mid_year_remark': '',
            'statement_scores-TOTAL_FORMS': '1',
            'statement_scores-INITIAL_FORMS': '1',
            'statement_scores-MIN_NUM_FORMS': '0',
            'statement_scores-MAX_NUM_FORMS': '1000',
            'statement_scores-0-id': str(self.statement_score.id),
            'statement_scores-0-end_year_score': '5',
        }

    def test_saved_supervisor_scores_are_visible_to_employee(self):
        response = self.client.post(
            reverse('supervisor_review', args=[self.appraisal.id]),
            self.review_data(),
        )

        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.target.refresh_from_db()
        self.statement_score.refresh_from_db()
        self.assertEqual(self.target.mid_year_progress, 'achieved')
        self.assertEqual(self.target.end_year_weight, 5)
        self.assertEqual(self.target.end_year_score, 4)
        self.assertEqual(self.statement_score.end_year_score, 5)

        self.client.force_login(self.employee.user)
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Target achieved')
        self.assertContains(response, '>4</td>')
        self.assertContains(response, '>5</td>')
        self.assertNotContains(response, 'End-year score:</strong>')

    def test_supervisor_review_uses_midyear_options_and_fixed_target_weight(self):
        response = self.client.get(
            reverse('supervisor_review', args=[self.appraisal.id]),
        )

        self.assertEqual(response.status_code, 200)
        for label in (
            'Fairly exhibited',
            'Poorly exhibited',
            'Well exhibited',
            'Excellently exhibited',
            'Exceptionally exhibited',
            'Maintain competency',
            'Change competency',
        ):
            self.assertContains(response, label)
        self.assertContains(response, 'score-radio-options')
        self.assertContains(response, 'flex-wrap: nowrap')
        self.assertContains(response, '<td>5</td>')
        self.assertNotContains(response, 'name="targets-0-end_year_weight"')

    def test_employee_role_department_reviewer_can_score_and_submit_to_hod(self):
        reviewer_user = User.objects.create_user(
            username='employee-role-reviewer',
            password='test-password',
        )
        reviewer = EmployeeProfile.objects.create(
            user=reviewer_user,
            role='Employee',
            department=self.employee.department,
        )
        DepartmentSupervisor.objects.create(
            department=self.employee.department,
            supervisor=reviewer,
        )
        hod_user = User.objects.create_user(
            username='employee-reviewer-hod',
            password='test-password',
        )
        hod = EmployeeProfile.objects.create(
            user=hod_user,
            role='HOD',
            department=self.employee.department,
        )
        AssignDepartmentHod.objects.create(
            department=self.employee.department,
            hod=hod,
        )
        self.appraisal.supervisor = reviewer
        self.appraisal.supervisor_comments = 'The employee met expectations.'
        self.appraisal.assessment_decision = 'suitable'
        self.appraisal.save(update_fields=[
            'supervisor',
            'supervisor_comments',
            'assessment_decision',
        ])
        self.target.end_year_score = 4
        self.target.save(update_fields=['end_year_score'])
        self.statement_score.end_year_score = 5
        self.statement_score.save(update_fields=['end_year_score'])
        self.client.force_login(reviewer_user)

        dashboard_response = self.client.get(reverse('dashboard_router'))
        self.assertRedirects(dashboard_response, reverse('supervisor_dashboard'))
        response = self.client.get(
            reverse('supervisor_review', args=[self.appraisal.id]),
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('supervisor_submit_to_hod', args=[self.appraisal.id]),
        )

        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_hod')
        self.assertEqual(self.appraisal.hod, hod)

    def test_hod_department_reviewer_can_review_in_hod_dashboard(self):
        reviewer = EmployeeProfile.objects.create(
            user=User.objects.create_user(
                username='hod-department-reviewer',
                password='test-password',
            ),
            role='HOD',
            department=self.employee.department,
        )
        DepartmentSupervisor.objects.create(
            department=self.employee.department,
            supervisor=reviewer,
        )
        next_hod = EmployeeProfile.objects.create(
            user=User.objects.create_user(
                username='next-hod-reviewer',
                password='test-password',
            ),
            role='HOD',
            department=self.employee.department,
        )
        AssignDepartmentHod.objects.create(
            department=self.employee.department,
            hod=next_hod,
        )
        self.appraisal.supervisor = reviewer
        self.appraisal.supervisor_comments = 'The employee met expectations.'
        self.appraisal.assessment_decision = 'suitable'
        self.appraisal.save(update_fields=[
            'supervisor',
            'supervisor_comments',
            'assessment_decision',
        ])
        self.target.end_year_score = 4
        self.target.save(update_fields=['end_year_score'])
        self.statement_score.end_year_score = 5
        self.statement_score.save(update_fields=['end_year_score'])
        self.client.force_login(reviewer.user)

        dashboard_response = self.client.get(reverse('hod_dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'Score appraisal')
        review_response = self.client.get(
            reverse('supervisor_review', args=[self.appraisal.id]),
        )
        self.assertEqual(review_response.status_code, 200)
        response = self.client.post(
            reverse('supervisor_submit_to_hod', args=[self.appraisal.id]),
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_hod')
        self.assertEqual(self.appraisal.hod, next_hod)

    def test_invalid_target_score_is_not_saved_and_validation_is_shown(self):
        response = self.client.post(
            reverse('supervisor_review', args=[self.appraisal.id]),
            self.review_data(target_score='-5'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Scores were not saved')
        self.assertContains(response, 'Target 1 — end_year_score')
        self.assertContains(response, 'Ensure this value is greater than or equal to 1')
        self.target.refresh_from_db()
        self.assertIsNone(self.target.end_year_score)
        self.assertIsNone(self.target.mid_year_progress)

    def test_supervisor_cannot_submit_with_unscored_target_or_statement(self):
        self.appraisal.supervisor_comments = 'The review is complete.'
        self.appraisal.assessment_decision = 'suitable'
        self.appraisal.save(update_fields=[
            'supervisor_comments',
            'assessment_decision',
        ])
        submit_url = reverse('supervisor_submit_to_hod', args=[self.appraisal.id])
        response = self.client.post(submit_url, follow=True)

        self.assertContains(response, 'Score every target before submitting this appraisal.')
        self.assertEqual(self.appraisal.status, 'pending_manager')

        self.target.end_year_score = 4
        self.target.save(update_fields=['end_year_score'])
        response = self.client.post(submit_url, follow=True)

        self.assertContains(
            response,
            'Score every selected statement for Communication.',
        )
        self.assertEqual(self.appraisal.status, 'pending_manager')


class AppraisalFinalScoreCalculationTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name='Performance')
        employee_user = User.objects.create_user(
            username='calculation-employee',
            password='test-password',
        )
        employee = EmployeeProfile.objects.create(
            user=employee_user,
            role='Employee',
            department=department,
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(employee=employee, cycle=cycle)
        AppraisalTarget.objects.create(
            appraisal=self.appraisal,
            key_result_area='Target A',
            target_description='Complete target A.',
            end_year_score=3,
        )
        AppraisalTarget.objects.create(
            appraisal=self.appraisal,
            key_result_area='Target B',
            target_description='Complete target B.',
            end_year_score=5,
        )

        core = Competency.objects.create(
            name='Core',
            competency_type='core',
            description='First core statement.\nSecond core statement.\nUnselected statement.',
        )
        core_score = self.appraisal.competency_scores.create(competency=core)
        core_score.statement_scores.create(
            statement_index=0,
            statement='First core statement.',
            end_year_score=3,
        )
        core_score.statement_scores.create(
            statement_index=1,
            statement='Second core statement.',
            end_year_score=4,
        )

        non_core = Competency.objects.create(
            name='Non-core',
            competency_type='non_core',
            description='First non-core statement.\nSecond non-core statement.',
        )
        non_core_score = self.appraisal.competency_scores.create(competency=non_core)
        non_core_score.statement_scores.create(
            statement_index=0,
            statement='First non-core statement.',
            end_year_score=2,
        )
        non_core_score.statement_scores.create(
            statement_index=1,
            statement='Second non-core statement.',
            end_year_score=4,
        )

    def test_final_score_uses_target_and_selected_statement_weights(self):
        self.appraisal.calculate_final_score()

        self.assertEqual(self.appraisal.overall_percentage, 75)
        self.assertEqual(self.appraisal.final_rating, 'Very Good')

    def test_final_score_requires_each_selected_statement_to_be_scored(self):
        statement_score = AppraisalCompetencyStatementScore.objects.filter(
            competency_score__appraisal=self.appraisal,
            competency_score__competency__competency_type='core',
        ).first()
        statement_score.end_year_score = None
        statement_score.save(update_fields=['end_year_score'])

        with self.assertRaises(ValidationError):
            self.appraisal.calculate_final_score()

    def test_live_calculation_is_rendered_on_supervisor_review(self):
        supervisor_user = User.objects.create_user(
            username='calculation-supervisor',
            password='test-password',
        )
        supervisor = EmployeeProfile.objects.create(
            user=supervisor_user,
            role='Supervisor',
            department=self.appraisal.employee.department,
        )
        self.appraisal.supervisor = supervisor
        self.appraisal.status = 'pending_manager'
        self.appraisal.save(update_fields=['supervisor', 'status'])
        self.client.force_login(supervisor_user)

        response = self.client.get(
            reverse('supervisor_review', args=[self.appraisal.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Live Overall Assessment')
        self.assertContains(response, 'A × 0.6')
        self.assertContains(response, 'Average of core competency group averages')
        self.assertContains(response, 'T ÷ 5 × 100')
        self.assertContains(response, 'First core statement.')
        self.assertContains(response, 'Second core statement.')
        self.assertNotContains(response, 'Unselected statement.')
        self.assertContains(response, 'data-weight="0.3"')


class AppraisalReturnWorkflowTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Quality Assurance')
        self.employee_user = User.objects.create_user(
            username='return-employee',
            password='test-password',
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            role='Employee',
            department=self.department,
        )
        self.supervisor_user = User.objects.create_user(
            username='return-supervisor',
            password='test-password',
        )
        self.supervisor = EmployeeProfile.objects.create(
            user=self.supervisor_user,
            role='Supervisor',
            department=self.department,
        )
        self.hod_user = User.objects.create_user(
            username='return-hod',
            password='test-password',
        )
        self.hod = EmployeeProfile.objects.create(
            user=self.hod_user,
            role='HOD',
            department=self.department,
        )
        self.hr_user = User.objects.create_user(
            username='return-hr',
            password='test-password',
        )
        EmployeeProfile.objects.create(
            user=self.hr_user,
            role='HR',
            department=self.department,
        )
        self.cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(
            employee=self.employee,
            supervisor=self.supervisor,
            hod=self.hod,
            cycle=self.cycle,
            status='pending_manager',
        )
        AppraisalTarget.objects.create(
            appraisal=self.appraisal,
            key_result_area='Complete records',
            target_description='Complete assigned records.',
            end_year_score=4,
        )
        competency = Competency.objects.create(
            name='Accuracy',
            competency_type='core',
            description='Check information carefully.',
        )
        competency_score = self.appraisal.competency_scores.create(
            competency=competency,
        )
        competency_score.statement_scores.create(
            statement_index=0,
            statement=competency.description_items[0],
            end_year_score=4,
        )
        DepartmentSupervisor.objects.create(
            department=self.department,
            supervisor=self.supervisor,
        )
        AssignDepartmentHod.objects.create(
            department=self.department,
            hod=self.hod,
        )

    def test_supervisor_must_comment_and_return_is_visible_to_employee(self):
        self.client.force_login(self.supervisor_user)
        url = reverse('return_appraisal', args=[self.appraisal.id])

        response = self.client.post(url, {'comment': '  '})
        self.assertRedirects(response, reverse('supervisor_review', args=[self.appraisal.id]))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_manager')
        self.assertFalse(AppraisalReturn.objects.filter(appraisal=self.appraisal).exists())

        response = self.client.post(url, {'comment': 'Please add measurable targets.'})
        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.appraisal.refresh_from_db()
        decision = self.appraisal.returns.get()
        self.assertEqual(self.appraisal.status, 'returned_employee')
        self.assertEqual(decision.to_status, 'returned_employee')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.employee_user,
            title='Appraisal returned for updates',
        ).exists())

        self.client.force_login(self.employee_user)
        response = self.client.get(reverse('fill_targets'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please add measurable targets.')

    def test_employee_can_correct_and_resubmit_to_original_supervisor(self):
        self.appraisal.status = 'returned_employee'
        self.cycle.is_active = False
        self.cycle.save(update_fields=['is_active'])
        self.appraisal.save(update_fields=['status'])
        competency = Competency.objects.create(
            name='Accuracy',
            competency_type='core',
            description='Check information carefully.',
        )
        self.appraisal.employee_comments = 'I achieved my service goals.'
        self.appraisal.competency_scores.create(competency=competency)
        self.appraisal.save(update_fields=['employee_comments'])
        self.client.force_login(self.employee_user)

        response = self.client.post(
            reverse('submit_to_supervisor', args=[self.appraisal.id]),
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_manager')
        self.assertEqual(self.appraisal.supervisor, self.supervisor)
        self.assertTrue(self.appraisal.competency_scores.filter(competency=competency).exists())

    def test_hod_returns_to_supervisor_and_supervisor_can_resubmit(self):
        self.appraisal.status = 'pending_hod'
        self.appraisal.supervisor_comments = 'The employee met expectations.'
        self.appraisal.assessment_decision = 'suitable'
        self.appraisal.save(update_fields=[
            'status',
            'supervisor_comments',
            'assessment_decision',
        ])
        self.client.force_login(self.hod_user)

        response = self.client.post(
            reverse('return_appraisal', args=[self.appraisal.id]),
            {'comment': 'Please correct the competency scores.'},
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'returned_supervisor')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.supervisor_user,
            title='Appraisal returned for updates',
        ).exists())

        self.client.force_login(self.supervisor_user)
        response = self.client.get(reverse('supervisor_review', args=[self.appraisal.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please correct the competency scores.')

        response = self.client.post(
            reverse('supervisor_submit_to_hod', args=[self.appraisal.id]),
        )
        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_hod')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.hod_user,
            title='Appraisal awaiting your review',
        ).exists())

    def test_supervisor_own_appraisal_routes_to_hod_and_hod_can_return_it(self):
        SupervisorReporting.objects.create(
            supervisor=self.supervisor,
            manager=self.hod,
        )
        appraisal = Appraisal.objects.create(
            employee=self.supervisor,
            cycle=self.cycle,
            status='draft',
            employee_comments='I completed my annual objectives.',
        )
        AppraisalTarget.objects.create(
            appraisal=appraisal,
            key_result_area='Improve service delivery',
            target_description='Meet all service targets.',
        )
        competency = Competency.objects.create(
            name='Leadership',
            competency_type='core',
            description='Support the team.',
        )
        appraisal.competency_scores.create(competency=competency)

        self.client.force_login(self.supervisor_user)
        response = self.client.post(
            reverse('submit_to_supervisor', args=[appraisal.id]),
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.status, 'pending_manager')
        self.assertEqual(appraisal.supervisor, self.hod)

        self.client.force_login(self.hod_user)
        response = self.client.post(
            reverse('return_appraisal', args=[appraisal.id]),
            {'comment': 'Please clarify the reported outcomes.'},
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.status, 'returned_employee')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.supervisor_user,
            title='Appraisal returned for updates',
        ).exists())

    def test_hod_own_appraisal_submits_directly_to_hr(self):
        appraisal = Appraisal.objects.create(
            employee=self.hod,
            cycle=self.cycle,
            status='draft',
            employee_comments='I completed my annual objectives.',
        )
        AppraisalTarget.objects.create(
            appraisal=appraisal,
            key_result_area='Improve service delivery',
            target_description='Meet all service targets.',
        )
        competency = Competency.objects.create(
            name='Leadership',
            competency_type='core',
            description='Support the team.',
        )
        competency_score = appraisal.competency_scores.create(competency=competency)
        statement_score = competency_score.statement_scores.create(
            statement_index=0,
            statement='Support the team.',
        )

        self.client.force_login(self.hod_user)
        response = self.client.post(
            reverse('submit_to_supervisor', args=[appraisal.id]),
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.status, 'pending_hr')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.hr_user,
            title='Appraisal awaiting HR approval',
        ).exists())

        self.client.force_login(self.hr_user)
        response = self.client.get(reverse('hr_approve', args=[appraisal.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HOD’s signature:')
        self.assertContains(response, self.hod_user.username)
        self.assertContains(response, 'Save HOD Scores')
        self.assertContains(response, 'HR Assessment of HOD Targets')
        self.assertNotContains(response, 'Mid-year progress')

        response = self.client.post(
            reverse('hr_approve', args=[appraisal.id]),
            {
                'action': 'save_hod_scores',
                'hod_targets-TOTAL_FORMS': '1',
                'hod_targets-INITIAL_FORMS': '1',
                'hod_targets-MIN_NUM_FORMS': '0',
                'hod_targets-MAX_NUM_FORMS': '1000',
                f'hod_targets-0-id': str(appraisal.targets.get().id),
                'hod_targets-0-end_year_assessment': 'completed',
                'hod_targets-0-end_year_weight': '5',
                'hod_targets-0-end_year_score': '4',
                'hod_targets-0-end_year_comment': 'very_good',
                'hod_statement_scores-TOTAL_FORMS': '1',
                'hod_statement_scores-INITIAL_FORMS': '1',
                'hod_statement_scores-MIN_NUM_FORMS': '0',
                'hod_statement_scores-MAX_NUM_FORMS': '1000',
                'hod_statement_scores-0-id': str(statement_score.id),
                'hod_statement_scores-0-end_year_score': '5',
            },
        )
        self.assertRedirects(response, reverse('hr_approve', args=[appraisal.id]))
        appraisal.targets.get().refresh_from_db()
        statement_score.refresh_from_db()
        self.assertEqual(appraisal.targets.get().end_year_score, 4)
        self.assertEqual(statement_score.end_year_score, 5)

        response = self.client.post(
            reverse('hr_approve', args=[appraisal.id]),
            {'hr_comments': 'Approved after review.'},
        )
        self.assertRedirects(response, reverse('hr_dashboard'))
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.status, 'completed')
        self.assertEqual(appraisal.overall_percentage, 78)

    def test_hod_scored_supervisor_appraisal_submits_to_hr(self):
        appraisal = Appraisal.objects.create(
            employee=self.supervisor,
            supervisor=self.hod,
            cycle=self.cycle,
            status='pending_manager',
            supervisor_comments='The supervisor performed well.',
            assessment_decision='suitable',
        )
        AppraisalTarget.objects.create(
            appraisal=appraisal,
            key_result_area='Improve service delivery',
            target_description='Meet all service targets.',
            end_year_score=4,
        )
        competency = Competency.objects.create(
            name='Leadership',
            competency_type='core',
            description='Support the team.',
        )
        competency_score = appraisal.competency_scores.create(competency=competency)
        competency_score.statement_scores.create(
            statement_index=0,
            statement='Support the team.',
            end_year_score=4,
        )

        self.client.force_login(self.hod_user)
        response = self.client.post(
            reverse('supervisor_submit_to_hod', args=[appraisal.id]),
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.status, 'pending_hr')
        self.assertEqual(appraisal.hod, self.hod)
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.hr_user,
            title='Appraisal awaiting HR approval',
        ).exists())

    def test_hod_review_shows_competency_description(self):
        competency = Competency.objects.create(
            name='Risk assessment',
            competency_type='core',
            description='Identify and assess operational risks.',
        )
        self.appraisal.competency_scores.create(competency=competency)
        self.appraisal.status = 'pending_hod'
        self.appraisal.save(update_fields=['status'])
        self.client.force_login(self.hod_user)

        response = self.client.get(reverse('hod_review', args=[self.appraisal.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identify and assess operational risks.')

    def test_hr_review_shows_competency_description(self):
        competency = Competency.objects.create(
            name='Risk assessment',
            competency_type='core',
            description='Identify and assess operational risks.',
        )
        self.appraisal.competency_scores.create(competency=competency)
        self.appraisal.status = 'pending_hr'
        self.appraisal.save(update_fields=['status'])
        self.client.force_login(self.hr_user)

        response = self.client.get(reverse('hr_approve', args=[self.appraisal.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identify and assess operational risks.')

    def test_hr_returns_to_hod_and_hod_can_resubmit(self):
        self.appraisal.status = 'pending_hr'
        self.appraisal.save(update_fields=['status'])
        self.client.force_login(self.hr_user)

        response = self.client.post(
            reverse('return_appraisal', args=[self.appraisal.id]),
            {'comment': 'Please clarify the final comments.'},
        )

        self.assertRedirects(response, reverse('hr_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'returned_hod')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.hod_user,
            title='Appraisal returned for updates',
        ).exists())

        self.client.force_login(self.hod_user)
        response = self.client.get(reverse('hod_review', args=[self.appraisal.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please clarify the final comments.')

        response = self.client.post(
            reverse('hod_review', args=[self.appraisal.id]),
            {'hod_comments': 'Updated final comments.'},
        )
        self.assertRedirects(response, reverse('hod_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_hr')
        self.assertTrue(AppNotification.objects.filter(
            recipient=self.hr_user,
            title='Appraisal awaiting HR approval',
        ).exists())
