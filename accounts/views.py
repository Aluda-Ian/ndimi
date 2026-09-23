from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from system.models import SystemSettings

from . import emails
from .forms import DeleteAccountForm, LoginForm, ProfileForm, SignUpForm
from .models import is_email_verified, mark_email_verified

User = get_user_model()


# ---------- sign up / in / out ----------

def signup(request):
    if request.user.is_authenticated:
        return redirect('home')

    site = SystemSettings.load()
    if not site.allow_signup:
        return render(request, 'accounts/signup_closed.html', {'site': site}, status=403)

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            if site.default_group_id:
                user.groups.add(site.default_group_id)
            login(request, user, backend='accounts.backends.EmailOrUsernameBackend')
            if emails.send_verification_email(request, user):
                messages.info(request, f'Welcome! We sent a link to {user.email} to confirm your email.')
            else:
                messages.warning(request, "Welcome! We couldn't send the confirmation email. You can resend it from your account page.")
            return redirect('home')
    else:
        form = SignUpForm()

    return render(request, 'accounts/signup.html', {'form': form})


class SignInView(auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get('remember_me'):
            self.request.session.set_expiry(0)  # end the session when the browser closes
        return response


@csrf_exempt  # Signing out can't harm the account, and a stale page token shouldn't trap anyone signed in.
def sign_out(request):
    """GET shows a confirmation page (so plain links work); POST signs out."""
    if request.method == 'POST':
        logout(request)
        messages.success(request, "You're signed out.")
        return redirect('login')
    if not request.user.is_authenticated:
        return redirect('login')
    return render(request, 'accounts/logout_confirm.html')


# ---------- account page ----------

@login_required
def account(request):
    user = request.user
    old_email = user.email
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save()
            if user.email.lower() != (old_email or '').lower():
                if emails.send_verification_email(request, user):
                    messages.info(request, f'Saved. We sent a link to {user.email} to confirm the new address.')
                else:
                    messages.warning(request, "Saved, but we couldn't send the confirmation email. Try resending it below.")
            else:
                messages.success(request, 'Your details are saved.')
            return redirect('account')
    else:
        form = ProfileForm(instance=user)

    return render(request, 'accounts/account.html', {
        'form': form,
        'email_verified': is_email_verified(user),
        'require_verification': SystemSettings.load().require_email_verification,
    })


@login_required
@require_POST
def resend_verification(request):
    user = request.user
    if is_email_verified(user):
        messages.info(request, 'Your email is already confirmed.')
    elif emails.send_verification_email(request, user):
        messages.success(request, f'We sent a new confirmation link to {user.email}.')
    else:
        messages.error(request, "We couldn't send the email. Please try again later or contact support.")
    return redirect(request.POST.get('next') or 'account')


def verify_email(request, token):
    data = emails.read_verify_token(token)
    user = None
    if data:
        user_id, email = data
        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None or user.email.lower() != email:
            user = None  # account gone, or the email changed since the link was sent
    if user is None:
        return render(request, 'accounts/verify_result.html', {'ok': False}, status=400)
    mark_email_verified(user)
    return render(request, 'accounts/verify_result.html', {'ok': True, 'verified_user': user})


# ---------- password ----------

class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'accounts/password_change_form.html'
    success_url = reverse_lazy('account')

    def form_valid(self, form):
        response = super().form_valid(form)  # keeps this session signed in, signs out others
        emails.send_password_changed_email(self.request, form.user)
        messages.success(self.request, 'Password changed. Your other devices have been signed out.')
        return response


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        emails.send_password_changed_email(self.request, form.user)
        # A reset link proves ownership of the email.
        if form.user.email:
            mark_email_verified(form.user)
        return response


# ---------- sessions & deletion ----------

@login_required
@require_POST
def sign_out_other_devices(request):
    user_id = str(request.user.pk)
    current = request.session.session_key
    removed = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key=current):
        if session.get_decoded().get('_auth_user_id') == user_id:
            session.delete()
            removed += 1
    messages.success(request, f'Signed out of {removed} other session{"s" if removed != 1 else ""}.')
    return redirect('account')


def _delete_user_projects(user_id):
    """Best effort: remove the user's projects from MongoDB."""
    from pymongo.errors import PyMongoError

    from projects.views import _mongo_database

    try:
        client, database = _mongo_database()
        database.projects.delete_many({'owner_id': user_id})
        client.close()
    except PyMongoError:
        pass


@login_required
def delete_account(request):
    user = request.user
    if user.is_superuser:
        messages.error(request, 'Superuser accounts can\'t be deleted here. Ask another superuser to do it in the admin panel.')
        return redirect('account')

    if request.method == 'POST':
        form = DeleteAccountForm(user, request.POST)
        if form.is_valid():
            _delete_user_projects(user.pk)
            logout(request)
            user.delete()
            messages.success(request, 'Your account has been deleted.')
            return redirect('login')
    else:
        form = DeleteAccountForm(user)
    return render(request, 'accounts/delete_account.html', {'form': form})
