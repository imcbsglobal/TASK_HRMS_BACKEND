from django.db import migrations, models


class Migration(migrations.Migration):
    """
    The `is_admin_user` column was added directly to the database outside of
    Django's migration system.  This migration records the field so Django's
    state matches the real schema without trying to ALTER TABLE (which would
    fail because the column already exists).
    """

    dependencies = [
        ('login', '0021_add_fcm_token_to_user'),
    ]

    operations = [
        # Use SeparateDatabaseAndState so Django updates its internal state to
        # know about the column, but issues NO actual SQL (the column is
        # already present in the database).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='user',
                    name='is_admin_user',
                    field=models.BooleanField(default=False),
                ),
            ],
            database_operations=[],   # nothing to run against the DB
        ),
    ]
