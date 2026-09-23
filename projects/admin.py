from django.contrib import admin

from .models import DemoSetup, Language

admin.site.site_header = 'Ndimi admin'
admin.site.site_title = 'Ndimi admin'
admin.site.index_title = 'Configuration'


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'available', 'enabled', 'sort_order')
    list_editable = ('available', 'enabled', 'sort_order')
    search_fields = ('name', 'code')


@admin.register(DemoSetup)
class DemoSetupAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')

    def has_add_permission(self, request):
        return not DemoSetup.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
