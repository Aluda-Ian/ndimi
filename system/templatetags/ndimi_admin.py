from django import template

from system.stats import dashboard_stats

register = template.Library()


@register.simple_tag(takes_context=True)
def ndimi_dashboard(context):
    return dashboard_stats(context['request'].user)
