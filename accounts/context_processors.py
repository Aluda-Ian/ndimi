def layout(request):
    """Signed-in users see account pages inside the dashboard shell."""
    user = getattr(request, 'user', None)
    in_app = bool(user and user.is_authenticated)
    return {
        'account_layout': 'accounts/layout_app.html' if in_app else 'accounts/layout_card.html',
        'in_app': in_app,
    }
