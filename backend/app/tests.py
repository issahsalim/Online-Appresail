from django.test import TestCase
from django.contrib.auth.models import User
from django.contrib import admin
from django.test.client import RequestFactory
from django.urls import reverse

from .models import (
    Appraisal,
    AppraisalCycle,
    AppraisalCompetencyScore,
    AppraisalReturn,
    AppraisalTarget,
    AppraisalTraining,
    AssignDepartmentHod,
    Competency,
    Department,
    EmployeeProfile,
    AssignSupervisor,
)


class EmployeeAppraisalVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='staff-001', password='test-password')
        self.profile = EmployeeProfile.objects.create(user=self.user)
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(
            employee=self.profile,
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
            mid_year_progress='demonstrated',
            end_year_score=5,
        )
        self.client.force_login(self.user)

    def test_employee_can_view_review_scores_after_submission_without_editing(self):
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Improve service')
        self.assertContains(response, 'Target achieved')
        self.assertContains(response, 'Good progress.')
        self.assertContains(response, 'Work effectively with colleagues toward shared goals.')
        self.assertContains(response, 'End-year score:</strong> 4')
        self.assertContains(response, 'End-year score:</strong> 5')

        response = self.client.post(reverse('fill_targets'), {
            'targets-TOTAL_FORMS': '1',
            'targets-INITIAL_FORMS': '1',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '1000',
        })

        self.assertRedirects(response, reverse('fill_targets'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.targets.get().end_year_score, 4)


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
        self.assertEqual(response.wsgi_request.user.pk, self.user.pk)


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
        self.assignment = AssignSupervisor.objects.create(
            department=self.department,
            employee=self.employee,
            supervisor=self.old_supervisor,
        )
        cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.draft_appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=cycle,
            supervisor=self.old_supervisor,
        )
        self.client.force_login(self.hr_user)

    def test_options_endpoint_only_returns_people_in_selected_department(self):
        response = self.client.get(
            reverse('hr_assignment_options'),
            {'department_id': self.department.id},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            {employee['id'] for employee in data['employees']},
            {self.employee.id, self.old_supervisor.id, self.new_supervisor.id,
             self.subordinate_supervisor.id},
        )
        self.assertEqual(
            {supervisor['id'] for supervisor in data['supervisors']},
            {
                self.old_supervisor.id,
                self.new_supervisor.id,
                self.subordinate_supervisor.id,
            },
        )
        self.assertNotIn(
            self.foreign_supervisor.id,
            [supervisor['id'] for supervisor in data['supervisors']],
        )

    def test_search_endpoint_filters_staff_id_department_and_related_names(self):
        response = self.client.get(
            reverse('hr_search_supervisor_assignments'),
            {'q': 'assigned-employee'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['results'][0]['employee_staff_id'], 'assigned-employee')

        response = self.client.get(
            reverse('hr_search_supervisor_assignments'),
            {'department': self.other_department.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 0)

        response = self.client.get(
            reverse('hr_search_supervisor_assignments'),
            {'q': 'old-supervisor'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)

    def test_search_endpoint_paginates_and_is_hr_only(self):
        for index in range(26):
            user = User.objects.create_user(
                username=f'page-employee-{index:02}',
                password='test-password',
            )
            employee = EmployeeProfile.objects.create(
                user=user,
                role='Employee',
                department=self.department,
            )
            AssignSupervisor.objects.create(
                department=self.department,
                employee=employee,
                supervisor=self.old_supervisor,
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
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'employee': self.employee.id,
            'supervisor': self.new_supervisor.id,
        })

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        assignments = AssignSupervisor.objects.filter(employee=self.employee)
        self.assertEqual(assignments.count(), 1)
        self.assertEqual(assignments.get().supervisor, self.new_supervisor)
        self.draft_appraisal.refresh_from_db()
        self.assertEqual(self.draft_appraisal.supervisor, self.new_supervisor)

    def test_hr_can_assign_a_supervisor_to_another_supervisor(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'employee': self.subordinate_supervisor.id,
            'supervisor': self.new_supervisor.id,
        })

        self.assertRedirects(response, reverse('hr_supervisor_assignments'))
        assignment = AssignSupervisor.objects.get(employee=self.subordinate_supervisor)
        self.assertEqual(assignment.supervisor, self.new_supervisor)

    def test_hr_cannot_assign_a_supervisor_to_themselves(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'employee': self.subordinate_supervisor.id,
            'supervisor': self.subordinate_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A supervisor cannot be assigned to themselves.')
        self.assertFalse(
            AssignSupervisor.objects.filter(employee=self.subordinate_supervisor).exists()
        )

    def test_hr_cannot_assign_supervisor_from_another_department(self):
        response = self.client.post(reverse('hr_supervisor_assignments'), {
            'department': self.department.id,
            'employee': self.employee.id,
            'supervisor': self.foreign_supervisor.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose a supervisor from the selected department.')
        self.assertEqual(
            AssignSupervisor.objects.filter(employee=self.employee).get().supervisor,
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
        self.assertContains(response, 'Completed Appraisals')
        self.assertContains(response, 'Cycle year')
        self.assertContains(response, 'Save Role Changes')
        self.assertContains(response, f'name="role_{self.employee.id}"')

    def test_hr_can_change_user_role_and_group_is_updated(self):
        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.employee.id}': 'Supervisor',
        })

        self.assertRedirects(response, reverse('hr_dashboard'))
        self.employee.refresh_from_db()
        self.employee.user.refresh_from_db()
        self.assertEqual(self.employee.role, 'Supervisor')
        self.assertEqual(
            set(self.employee.user.groups.values_list('name', flat=True)),
            {'Supervisor'},
        )

    def test_hr_cannot_assign_hr_role_or_update_an_hr_profile(self):
        response = self.client.post(reverse('hr_dashboard'), {
            f'role_{self.employee.id}': 'HR',
        })

        self.assertRedirects(response, reverse('hr_dashboard'))
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
        AssignSupervisor.objects.create(
            department=self.department,
            employee=self.employee,
            supervisor=self.supervisor,
        )
        self.client.force_login(self.employee_user)

    def test_selection_page_shows_competency_descriptions(self):
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Communicate clearly with colleagues and customers.')
        self.assertContains(response, '<li>Communicate clearly with colleagues and customers.</li>')
        self.assertContains(response, '<li>Listen actively and respond constructively.</li>')
        self.assertContains(response, 'Organize work and manage workload systematically.')
        self.assertContains(response, '01/01/2026')
        self.assertContains(response, '31/12/2026')

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
        self.assertNotContains(response, 'Planning')

    def test_employee_must_select_a_competency_before_submission(self):
        response = self.client.post(
            reverse('submit_to_supervisor', args=[self.appraisal.id]),
        )

        self.assertRedirects(response, reverse('fill_targets'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'draft')

    def test_save_and_submit_persists_selected_competencies_and_submits_appraisal(self):
        response = self.client.post(
            reverse('fill_targets'),
            {
                **self.competency_selection_data([self.competencies[0].id]),
                'action': 'submit',
            },
        )

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_manager')
        self.assertEqual(self.appraisal.supervisor, self.supervisor)
        self.assertTrue(self.appraisal.competency_scores.filter(
            competency=self.competencies[0],
        ).exists())

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
        self.assertNotContains(response, 'Communication')
        self.assertNotContains(response, 'Select Competencies')


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
            role='Supervisor',
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
        AssignSupervisor.objects.create(
            department=self.department,
            employee=self.supervisor,
            supervisor=self.manager,
        )

        response = self.client.get(reverse('supervisor_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Own Appraisal')
        self.assertEqual(response.context['my_appraisal'].employee, self.supervisor)


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
            AssignSupervisor.objects.create(
                department=department,
                employee=employee,
                supervisor=self.supervisor,
            )
            self.employees.append(employee)
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
            'targets-0-end_year_weight': '5',
            'targets-0-end_year_score': target_score,
            'targets-0-end_year_comment': '',
            'competency_scores-TOTAL_FORMS': '1',
            'competency_scores-INITIAL_FORMS': '1',
            'competency_scores-MIN_NUM_FORMS': '0',
            'competency_scores-MAX_NUM_FORMS': '1000',
            'competency_scores-0-id': str(self.competency_score.id),
            'competency_scores-0-mid_year_progress': 'demonstrated',
            'competency_scores-0-mid_year_remark': '',
            'competency_scores-0-end_year_score': '5',
        }

    def test_saved_supervisor_scores_are_visible_to_employee(self):
        response = self.client.post(
            reverse('supervisor_review', args=[self.appraisal.id]),
            self.review_data(),
        )

        self.assertRedirects(response, reverse('supervisor_dashboard'))
        self.target.refresh_from_db()
        self.competency_score.refresh_from_db()
        self.assertEqual(self.target.mid_year_progress, 'achieved')
        self.assertEqual(self.target.end_year_score, 4)
        self.assertEqual(self.competency_score.end_year_score, 5)

        self.client.force_login(self.employee.user)
        response = self.client.get(reverse('fill_targets'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Target achieved')
        self.assertContains(response, 'End-year score:</strong> 4')
        self.assertContains(response, 'End-year score:</strong> 5')

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
        AssignSupervisor.objects.create(
            department=self.department,
            employee=self.employee,
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

        self.client.force_login(self.employee_user)
        response = self.client.get(reverse('fill_targets'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please add measurable targets.')

    def test_employee_can_correct_and_resubmit_to_original_supervisor(self):
        self.appraisal.status = 'returned_employee'
        self.appraisal.save(update_fields=['status'])
        competency = Competency.objects.create(name='Accuracy', competency_type='core')
        self.client.force_login(self.employee_user)

        response = self.client.post(reverse('fill_targets'), {
            'targets-TOTAL_FORMS': '5',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '5',
            'trainings-TOTAL_FORMS': '4',
            'trainings-INITIAL_FORMS': '0',
            'trainings-MIN_NUM_FORMS': '0',
            'trainings-MAX_NUM_FORMS': '4',
            'competencies': [str(competency.id)],
            'action': 'submit',
        })

        self.assertRedirects(response, reverse('employee_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'pending_manager')
        self.assertEqual(self.appraisal.supervisor, self.supervisor)
        self.assertTrue(self.appraisal.competency_scores.filter(competency=competency).exists())

    def test_hod_returns_to_supervisor_and_supervisor_can_resubmit(self):
        self.appraisal.status = 'pending_hod'
        self.appraisal.save(update_fields=['status'])
        self.client.force_login(self.hod_user)

        response = self.client.post(
            reverse('return_appraisal', args=[self.appraisal.id]),
            {'comment': 'Please correct the competency scores.'},
        )

        self.assertRedirects(response, reverse('hod_dashboard'))
        self.appraisal.refresh_from_db()
        self.assertEqual(self.appraisal.status, 'returned_supervisor')

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
