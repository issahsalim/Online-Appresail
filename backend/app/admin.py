from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import User
from .models import (
    Department, 
    EmployeeProfile, 
    DepartmentSupervisor,
    SupervisorReporting,
    AssignDepartmentHod,
    AppraisalCycle,
    Competency,
    Appraisal,
    AppraisalTraining,
    AppraisalReturn,
    AppraisalTarget,
    AppraisalCompetencyScore,
    AppraisalCompetencyStatementScore,
    AuditLog,
)

# ==========================================
# 1. USER & PROFILE ADMIN (Auto-Search)
# ==========================================

# First, we tell the built-in User Admin to allow searching by Staff ID (username)
class StaffIdUserCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Staff ID'


class StaffIdUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Staff ID'


class CustomUserAdmin(UserAdmin):
    add_form = StaffIdUserCreationForm
    form = StaffIdUserChangeForm
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('username', 'password1', 'password2')}),
    )
    search_fields = ('username', 'first_name', 'last_name', 'email')
    list_display = ('staff_id', 'email', 'first_name', 'last_name', 'is_staff')

    @admin.display(description='Staff ID', ordering='username')
    def staff_id(self, obj):
        return obj.username

# Unregister the default User admin and register our custom one
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    # THIS enables the auto-search text box for the User field!
    autocomplete_fields = ['user']
    
    list_display = ('user', 'department', 'role')
    list_filter = ('role', 'department')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email')
    
    # Prevents N+1 queries when loading the list
    list_select_related = ('user', 'department')


# ==========================================
# 2. ASSIGNMENT TABLES (Department Filtering)
# ==========================================

@admin.register(DepartmentSupervisor)
class DepartmentSupervisorAdmin(admin.ModelAdmin):
    autocomplete_fields = ['supervisor']
    list_display = ('department', 'supervisor')
    list_filter = ('department',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'supervisor':
            queryset = EmployeeProfile.objects.exclude(role='HR')
            department_id = request.GET.get('department')
            if department_id:
                queryset = queryset.filter(department_id=department_id)
            kwargs["queryset"] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(SupervisorReporting)
class SupervisorReportingAdmin(admin.ModelAdmin):
    autocomplete_fields = ['supervisor', 'manager']
    list_display = ('supervisor', 'manager')
    search_fields = (
        'supervisor__user__username',
        'manager__user__username',
    )

@admin.register(AssignDepartmentHod)
class AssignDepartmentHodAdmin(admin.ModelAdmin):
    autocomplete_fields = ['hod']
    list_display = ('department', 'hod')
    list_filter = ('department',)


# ==========================================
# 3. MASTER DATA & CYCLE ADMIN
# ==========================================

@admin.register(AppraisalCycle)
class AppraisalCycleAdmin(admin.ModelAdmin):
    list_display = ('year', 'report_period_from', 'report_period_to', 'is_active')
    list_filter = ('is_active',)

@admin.register(Competency)
class CompetencyAdmin(admin.ModelAdmin):
    list_display = ('name', 'competency_type', 'display_order')
    list_editable = ('display_order',)
    list_filter = ('competency_type',)
    search_fields = ('name',)
    ordering = ('competency_type', 'display_order', 'name')


# ==========================================
# 4. APPRAISAL ADMIN (Inlines & Read-Only Rules)
# ==========================================

# These Inlines allow you to see Targets and Scores directly inside the Appraisal page
class AppraisalTargetInline(admin.TabularInline):
    model = AppraisalTarget
    extra = 0

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

class AppraisalCompetencyScoreInline(admin.TabularInline):
    model = AppraisalCompetencyScore
    extra = 0
    autocomplete_fields = ['competency']
    fields = ('competency', 'mid_year_progress', 'mid_year_remark')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return self.fields
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AppraisalCompetencyStatementScore)
class AppraisalCompetencyStatementScoreAdmin(admin.ModelAdmin):
    list_display = ('statement', 'competency_score', 'end_year_score')
    list_filter = (
        'competency_score__competency__competency_type',
        'competency_score__appraisal__status',
    )
    search_fields = (
        'statement',
        'competency_score__competency__name',
        'competency_score__appraisal__employee__user__username',
    )
    list_select_related = (
        'competency_score__competency',
        'competency_score__appraisal__employee__user',
    )

    def get_readonly_fields(self, request, obj=None):
        if (
            obj
            and obj.competency_score.appraisal.status == 'completed'
            and not request.user.is_superuser
        ):
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)


class AppraisalReturnInline(admin.TabularInline):
    model = AppraisalReturn
    extra = 0
    can_delete = False
    fields = ('reviewer_name', 'stage', 'from_status', 'to_status', 'comment', 'created_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class AppraisalTrainingInline(admin.TabularInline):
    model = AppraisalTraining
    extra = 0

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Appraisal)
class AppraisalAdmin(admin.ModelAdmin):
    list_display = ('employee', 'cycle', 'status', 'overall_percentage', 'final_rating')
    list_filter = ('status', 'cycle')
    
    # Auto-search for the people involved
    autocomplete_fields = ['employee', 'supervisor', 'hod']
    
    # Prevent N+1 queries
    list_select_related = ('employee__user', 'supervisor__user', 'cycle')
    
    # Attach the inlines
    inlines = [
        AppraisalTargetInline,
        AppraisalCompetencyScoreInline,
        AppraisalTrainingInline,
        AppraisalReturnInline,
    ]

    # SECURITY: If the appraisal is completed, HR shouldn't be able to edit it in the admin.
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'completed' and not request.user.is_superuser:
            return [f.name for f in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'actor_username',
        'actor_role',
        'action',
        'model_name',
        'object_id',
        'note',
        'ip_address',
        'actor_is_superuser',
    )
    list_filter = ('action', 'model_name', 'actor_is_superuser', 'created_at')
    search_fields = ('actor_username', 'model_name', 'object_id', 'object_label')
    date_hierarchy = 'created_at'
    ordering = ('-created_at', '-pk')

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False
        