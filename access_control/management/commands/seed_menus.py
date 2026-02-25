from django.core.management.base import BaseCommand
from access_control.models import Menu


class Command(BaseCommand):
    help = 'Seeds menu items matching the Sidebar.jsx structure'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding menus to match Sidebar.jsx...')

        # Clear existing menus (optional - uncomment if you want fresh start)
        # Menu.objects.all().delete()
        # self.stdout.write(self.style.WARNING('Cleared existing menus'))

        # Define menus that match EXACTLY with Sidebar.jsx
        # Sidebar has 6 menus: Dashboard, Employee Management, Interview Process,
        # Attendance (2 children), User Management (2 children), Master (2 children)
        # NOTE: Certificates has been REMOVED - it does not exist in Sidebar.jsx
        menus_data = [
            # 1. Dashboard — direct route, no children
            {
                'name': 'Dashboard',
                'slug': 'dashboard',
                'icon': '📊',
                'route': '/dashboard',
                'order': 1,
                'children': []
            },
            # 2. Employee Management — direct route, no children
            {
                'name': 'Employee Management',
                'slug': 'employee-management',
                'icon': '👥',
                'route': '/employees',
                'order': 2,
                'children': []
            },
            # 3. Interview Process — direct route, no children
            {
                'name': 'Interview Process',
                'slug': 'interviews',
                'icon': '💼',
                'route': '/interviews',
                'order': 3,
                'children': []
            },
            # 4. Attendance — parent with 2 children (key: "attendance" in Sidebar.jsx)
            {
                'name': 'Attendance',
                'slug': 'attendance',
                'icon': '⏰',
                'route': None,  # Parent menu has no route, only children do
                'order': 4,
                'children': [
                    {
                        'name': 'Admin View',
                        'slug': 'attendance-admin',
                        'icon': '👨‍💼',
                        'route': '/attendance/admin',
                        'order': 1
                    },
                    {
                        'name': 'My Attendance',
                        'slug': 'attendance-user',
                        'icon': '👤',
                        'route': '/attendance/user',
                        'order': 2
                    },
                ]
            },
            # 5. User Management — parent with 2 children (key: "user-management" in Sidebar.jsx)
            {
                'name': 'User Management',
                'slug': 'user-management',
                'icon': '👤',
                'route': None,  # Parent menu has no route, only children do
                'order': 5,
                'children': [
                    {
                        'name': 'User List',
                        'slug': 'user-list',
                        'icon': '📋',
                        'route': '/user-management/user-list',
                        'order': 1
                    },
                    {
                        'name': 'User Control',
                        'slug': 'user-control',
                        'icon': '🔐',
                        'route': '/user-management/user-control',
                        'order': 2
                    },
                ]
            },
            # 6. Master — parent with 2 children (key: "master" in Sidebar.jsx)
            {
                'name': 'Master',
                'slug': 'master',
                'icon': '⚙️',
                'route': None,  # Parent menu has no route, only children do
                'order': 6,
                'children': [
                    {
                        'name': 'Department',
                        'slug': 'department',
                        'icon': '🏢',
                        'route': '/master/department',
                        'order': 1
                    },
                    {
                        'name': 'Leave Type',
                        'slug': 'leave-type',
                        'icon': '📋',
                        'route': '/master/leave-type',
                        'order': 2
                    },
                ]
            },
        ]

        created_count = 0
        updated_count = 0

        for menu_data in menus_data:
            children = menu_data.pop('children', [])

            # Create or update parent menu
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

            # Create child menus
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
        self.stdout.write('Menu routes available:')
        self.stdout.write('  • /dashboard')
        self.stdout.write('  • /employees')
        self.stdout.write('  • /interviews')
        self.stdout.write('  • /attendance/admin')
        self.stdout.write('  • /attendance/user')
        self.stdout.write('  • /user-management/user-list')
        self.stdout.write('  • /user-management/user-control')
        self.stdout.write('  • /master/department')
        self.stdout.write('  • /master/leave-type')