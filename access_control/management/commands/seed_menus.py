from django.core.management.base import BaseCommand
from access_control.models import Menu


class Command(BaseCommand):
    help = 'Seeds menu items matching the Sidebar.jsx structure'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding menus to match Sidebar.jsx...')

        menus_data = [
            # 1. Dashboard
            {
                'name': 'Dashboard',
                'slug': 'dashboard',
                'icon': '📊',
                'route': '/dashboard',
                'order': 1,
                'children': []
            },
            # 2. Employee Management
            {
                'name': 'Employee Management',
                'slug': 'employee-management',
                'icon': '👥',
                'route': '/employees',
                'order': 2,
                'children': []
            },
            # 3. Recruitment (parent with 4 children)
            {
                'name': 'Recruitment',
                'slug': 'recruitment',
                'icon': '💼',
                'route': None,
                'order': 3,
                'children': [
                    {
                        'name': 'Upload CV',
                        'slug': 'recruitment-upload-cv',
                        'icon': '📤',
                        'route': '/recruitment/upload-cv',
                        'order': 1,
                    },
                    {
                        'name': 'Interview Process',
                        'slug': 'interviews',
                        'icon': '🤝',
                        'route': '/interviews',
                        'order': 2,
                    },
                    {
                        'name': 'Offer Letter',
                        'slug': 'recruitment-offer-letter',
                        'icon': '📄',
                        'route': '/recruitment/offer-letter',
                        'order': 3,
                    },
                ]
            },
            # 4. Attendance (parent with 2 children)
            {
                'name': 'Attendance',
                'slug': 'attendance',
                'icon': '⏰',
                'route': None,
                'order': 4,
                'children': [
                    {
                        'name': 'Admin View',
                        'slug': 'attendance-admin',
                        'icon': '👨‍💼',
                        'route': '/attendance/admin',
                        'order': 1,
                    },
                    {
                        'name': 'My Attendance',
                        'slug': 'attendance-user',
                        'icon': '👤',
                        'route': '/attendance/user',
                        'order': 2,
                    },
                ]
            },
            # 5. Increment Log
            {
                'name': 'Increment Log',
                'slug': 'increment-log',
                'icon': '📈',
                'route': '/increment-log',
                'order': 5,
                'children': []
            },
            # 6. Payroll
            {
                'name': 'Payroll',
                'slug': 'payroll',
                'icon': '💰',
                'route': '/payroll',
                'order': 6,
                'children': []
            },
            # 7. Offboarding (parent with 2 children)
            {
                'name': 'Offboarding',
                'slug': 'offboarding',
                'icon': '🚪',
                'route': None,
                'order': 7,
                'children': [
                    {
                        'name': 'Offboarding',
                        'slug': 'offboarding-list',
                        'icon': '🚪',
                        'route': '/offboarding',
                        'order': 1,
                    },
                    {
                        'name': 'Experience Certificate',
                        'slug': 'experience-certificate',
                        'icon': '🎓',
                        'route': '/recruitment/experience-certificate',
                        'order': 2,
                    },
                ]
            },
            # 8. Master (parent with 8 children)
            {
                'name': 'Master',
                'slug': 'master',
                'icon': '⚙️',
                'route': None,
                'order': 8,
                'children': [
                    {
                        'name': 'Department',
                        'slug': 'department',
                        'icon': '🏢',
                        'route': '/master/department',
                        'order': 1,
                    },
                    {
                        'name': 'Leave Type',
                        'slug': 'leave-type',
                        'icon': '📋',
                        'route': '/master/leave-type',
                        'order': 2,
                    },
                    {
                        'name': 'Allowance',
                        'slug': 'allowance',
                        'icon': '💵',
                        'route': '/master/allowance',
                        'order': 3,
                    },
                    {
                        'name': 'Deduction',
                        'slug': 'deduction',
                        'icon': '📉',
                        'route': '/master/deduction',
                        'order': 4,
                    },
                    {
                        'name': 'Holiday',
                        'slug': 'holiday',
                        'icon': '🎉',
                        'route': '/master/holiday',
                        'order': 5,
                    },
                    {
                        'name': 'Announcements',
                        'slug': 'announcements',
                        'icon': '📢',
                        'route': '/master/announcements',
                        'order': 6,
                    },
                    {
                        'name': 'Job Title',
                        'slug': 'job-title',
                        'icon': '🧑‍💼',
                        'route': '/master/job-title',
                        'order': 7,
                    },
                    {
                        'name': 'Section',
                        'slug': 'section',
                        'icon': '🗂️',
                        'route': '/master/section',
                        'order': 8,
                    },
                ]
            },
            # 9. User Management (parent with 2 children)
            {
                'name': 'User Management',
                'slug': 'user-management',
                'icon': '👤',
                'route': None,
                'order': 9,
                'children': [
                    {
                        'name': 'User List',
                        'slug': 'user-list',
                        'icon': '📋',
                        'route': '/user-management/user-list',
                        'order': 1,
                    },
                    {
                        'name': 'User Control',
                        'slug': 'user-control',
                        'icon': '🔐',
                        'route': '/user-management/user-control',
                        'order': 2,
                    },
                ]
            },
            # 10. Company Settings
            {
                'name': 'Company Settings',
                'slug': 'company-settings',
                'icon': '🏭',
                'route': '/company-settings',
                'order': 10,
                'children': []
            },
            # 11. WhatsApp Config
            {
                'name': 'WhatsApp Config',
                'slug': 'whatsapp-config',
                'icon': '💬',
                'route': '/whatsapp-config',
                'order': 11,
                'children': []
            },
            # 12. Settings (parent with 3 children)
            {
                'name': 'Settings',
                'slug': 'settings',
                'icon': '🔧',
                'route': None,
                'order': 12,
                'children': [
                    {
                        'name': 'Payroll Settings',
                        'slug': 'payroll-settings',
                        'icon': '💳',
                        'route': '/payroll-settings/basic-settings',
                        'order': 1,
                    },
                    {
                        'name': 'Geofence Settings',
                        'slug': 'geofence-settings',
                        'icon': '📍',
                        'route': '/settings/geofence',
                        'order': 2,
                    },
                    {
                        'name': 'Yearly Calendar',
                        'slug': 'yearly-calendar',
                        'icon': '📅',
                        'route': '/settings/yearly-calendar',
                        'order': 3,
                    },
                ]
            },
        ]

        created_count = 0
        updated_count = 0

        for menu_data in menus_data:
            children = menu_data.pop('children', [])

            parent_menu, created = Menu.objects.update_or_create(
                slug=menu_data['slug'],
                defaults=menu_data
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✓ Created menu: {parent_menu.name}'))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f'↻ Updated menu: {parent_menu.name}'))

            for child_data in children:
                child_data['parent'] = parent_menu
                child_menu, child_created = Menu.objects.update_or_create(
                    slug=child_data['slug'],
                    defaults=child_data
                )

                if child_created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Created submenu: {child_menu.name} ({child_menu.route})'))
                else:
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f'  ↻ Updated submenu: {child_menu.name} ({child_menu.route})'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Successfully seeded menus!'))
        self.stdout.write(self.style.SUCCESS(f'Created: {created_count} | Updated: {updated_count}'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('')
        self.stdout.write('All menu routes:')
        routes = [
            '/dashboard', '/employees',
            '/recruitment/upload-cv', '/interviews', '/recruitment/offer-letter',
            '/attendance/admin', '/attendance/user',
            '/increment-log', '/payroll',
            '/offboarding', '/recruitment/experience-certificate',
            '/master/department', '/master/leave-type', '/master/allowance',
            '/master/deduction', '/master/holiday', '/master/announcements',
            '/master/job-title', '/master/section',
            '/user-management/user-list', '/user-management/user-control',
            '/company-settings', '/whatsapp-config',
            '/payroll-settings/basic-settings', '/settings/geofence', '/settings/yearly-calendar',
        ]
        for r in routes:
            self.stdout.write(f'  • {r}')
