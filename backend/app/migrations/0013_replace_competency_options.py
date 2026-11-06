from django.db import migrations


COMPETENCIES = [
    ('core', 'Organization and Management', [
        'Ability to plan, organize and manage work load.',
        'Ability to work systematically and maintain quality.',
        'Ability to manage others to achieve shared goals.',
    ]),
    ('core', 'Innovation and Strategic Thinking', [
        'Support for organizational change.',
        'Ability to think broadly and demonstrate creativity.',
        'Originality in thinking.',
    ]),
    ('core', 'Leadership and Decision Making', [
        'Ability to initiate action and provide direction to others.',
        'Acceptance of responsibility and decision-making.',
        'Ability to exercise good judgment.',
    ]),
    ('core', 'Developing and Improving', [
        'Commitment to organization development.',
        'Commitment to customer satisfaction.',
        'Commitment to personnel development.',
    ]),
    ('core', 'Communication (oral, written & electronic)', [
        'Ability to communicate decisions clearly and fluently.',
        'Ability to negotiate and manage conflict effectively.',
        'Ability to relate and network across different levels and departments.',
    ]),
    ('core', 'Job Knowledge and Technical Skills', [
        'Demonstration of correct mental, physical and manual skills.',
        'Demonstration of cross-functional awareness.',
        'Building, applying and sharing of necessary expertise and technology.',
    ]),
    ('core', 'Supporting and Cooperating', [
        'Ability to work effectively with teams, clients and staff.',
        'Ability to show support to others.',
        "Ability to adhere to organization's principles, ethics and values.",
    ]),
    ('core', 'Maximizing and Maintaining Productivity', [
        'Ability to motivate and instigate others.',
        'Ability to accept challenges and execute them with confidence.',
        'Ability to manage pressure and setbacks effectively.',
    ]),
    ('core', 'Developing / Managing Budgets and Saving Cost', [
        'Firm awareness of financial issues and accountabilities.',
        'Understanding of business processes and customer priorities.',
        'Executing result-based actions.',
    ]),
    ('non_core', 'Ability to Develop Staff', [
        'Able to develop others (subordinates).',
        'Able to provide guidance and support to staff for their development.',
    ]),
    ('non_core', 'Commitment to Own Personal Development and Training', [
        'Eagerness for self-development.',
        'Inner drive to supplement training from organization.',
    ]),
    ('non_core', 'Delivering Results and Ensuring Customer Satisfaction', [
        'Ensuring customer satisfaction.',
        'Ensuring the delivery of quality service and products.',
    ]),
    ('non_core', 'Following Instructions and Working Towards Organisational Goals', [
        'Keeping to laid-down regulations and procedures.',
        'Willingness to act on customer feedback for customer satisfaction.',
    ]),
    ('non_core', 'Respect and Commitment', [
        'Respect for superiors, colleagues and customers.',
        'Commitment to work and Organizational Development.',
    ]),
    ('non_core', 'Ability to Work Effectively in a Team', [
        'Ability to function in a team.',
        'Ability to work in a team.',
    ]),
]


def replace_competencies(apps, schema_editor):
    Competency = apps.get_model('app', 'Competency')
    AppraisalCompetencyScore = apps.get_model('app', 'AppraisalCompetencyScore')
    database = schema_editor.connection.alias
    existing = list(Competency.objects.using(database).all().order_by('pk'))
    retained_ids = set()

    for competency_type, name, statements in COMPETENCIES:
        matches = [
            competency for competency in existing
            if competency.competency_type == competency_type
            and competency.name.casefold() == name.casefold()
        ]
        if len(matches) > 1:
            raise RuntimeError(f'Duplicate competency records found for {name}.')

        if matches:
            competency = matches[0]
            competency.name = name
            competency.description = '\n'.join(statements)
            competency.save(using=database, update_fields=['name', 'description'])
        else:
            competency = Competency.objects.using(database).create(
                competency_type=competency_type,
                name=name,
                description='\n'.join(statements),
            )
        retained_ids.add(competency.pk)

    obsolete = Competency.objects.using(database).exclude(pk__in=retained_ids)
    referenced_ids = list(
        AppraisalCompetencyScore.objects.using(database)
        .filter(competency_id__in=obsolete.values('pk'))
        .values_list('competency_id', flat=True)
        .distinct()
    )
    if referenced_ids:
        raise RuntimeError(
            'Cannot remove obsolete competencies that are used by appraisals: '
            f'{referenced_ids}'
        )
    obsolete.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0012_alter_departmentsupervisor_supervisor'),
    ]

    operations = [
        migrations.RunPython(replace_competencies, migrations.RunPython.noop),
    ]
