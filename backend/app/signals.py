from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Group
from .models import EmployeeProfile

@receiver(post_save, sender=EmployeeProfile)
def manage_user_groups(sender, instance, **kwargs):
    """
    Automatically puts the user in the correct Django Group based on their role.
    """
    roles = ['Employee', 'Supervisor', 'HOD', 'HR']
    
    # Ensure all groups exist in the database
    for role in roles:
        Group.objects.get_or_create(name=role)
        
    # Clear old groups so they don't have conflicting permissions
    instance.user.groups.clear()
    
    # Assign the new group based on their role
    group = Group.objects.get(name=instance.role)
    instance.user.groups.add(group)