from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse

from .models import (
    Appraisal,
    AppraisalCycle,
    AppraisalTarget,
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
        competency = Competency.objects.create(name='Teamwork', competency_type='core')
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
        self.cycle = AppraisalCycle.objects.create(year=2026, is_active=True)
        self.appraisal = Appraisal.objects.create(
            employee=self.employee,
            cycle=self.cycle,
        )
        self.competencies = [
            Competency.objects.create(name='Communication', competency_type='core'),
            Competency.objects.create(name='Planning', competency_type='non_core'),
        ]
        AssignSupervisor.objects.create(
            department=self.department,
            employee=self.employee,
            supervisor=self.supervisor,
        )
        self.client.force_login(self.employee_user)

    def competency_selection_data(self, competency_ids):
        return {
            'targets-TOTAL_FORMS': '5',
            'targets-INITIAL_FORMS': '0',
            'targets-MIN_NUM_FORMS': '0',
            'targets-MAX_NUM_FORMS': '5',
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
        competency = Competency.objects.create(name='Communication', competency_type='core')
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
