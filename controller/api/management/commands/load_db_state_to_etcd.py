from django.core.managemet.base import BaseCommand
from api.models import Key, App, Domain, Certificate, Config


class Command(BaseCommand):
    def handle(self, *args, **options):
        for M in (Key, App, Domain, Certificate, Config):
            for o in M.objects.all():
                o.save()
