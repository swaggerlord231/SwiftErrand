from decimal import Decimal

from django import forms

from .models import Errand, Review, User

MONEY_INPUT = {
    'class': 'wallet-input',
    'placeholder': '0.00',
    'min': '100',
    'step': '0.01',
}


class ErrandForm(forms.ModelForm):
    class Meta:
        model = Errand
        fields = [
            'title',
            'description',
            'pickup_address',
            'dropoff_address',
            'category',
            'urgency',
            'price',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Give your errand a short, descriptive title'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 5}),
            'pickup_address': forms.TextInput(attrs={'class': 'form-input'}),
            'dropoff_address': forms.TextInput(attrs={'class': 'form-input'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'urgency': forms.Select(attrs={'class': 'form-select'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'min': '100', 'step': '0.01'}),
        }

    def clean_price(self):
        price = self.cleaned_data['price']
        if price < 100:
            raise forms.ValidationError('The minimum price for an errand is ₦100.')
        return price


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.Select(attrs={'class': 'form-select'}),
            'comment': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 4, 'placeholder': 'Tell us about your experience'}),
        }


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }


class DepositForm(forms.Form):
    amount = forms.DecimalField(
        label='Amount (NGN)',
        min_value=Decimal('100.00'),
        max_value=Decimal('1000000.00'),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs=MONEY_INPUT),
        error_messages={
            'min_value': 'The minimum deposit is ₦100.',
            'max_value': 'The maximum deposit is ₦1,000,000.',
            'invalid': 'Enter a valid amount.',
        },
    )


class WithdrawForm(forms.Form):
    amount = forms.DecimalField(
        label='Amount (NGN)',
        min_value=Decimal('100.00'),
        max_value=Decimal('1000000.00'),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs=MONEY_INPUT),
        error_messages={
            'min_value': 'The minimum withdrawal is ₦100.',
            'max_value': 'The maximum withdrawal is ₦1,000,000.',
            'invalid': 'Enter a valid amount.',
        },
    )
    bank_name = forms.CharField(
        label='Bank',
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'wallet-input', 'placeholder': 'e.g. GTBank'}),
    )
    account_name = forms.CharField(
        label='Account name',
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'wallet-input', 'placeholder': 'Name on the account'}),
    )
    account_number = forms.RegexField(
        label='Account number',
        regex=r'^\d{10}$',
        widget=forms.TextInput(attrs={'class': 'wallet-input', 'placeholder': '10-digit account number', 'inputmode': 'numeric'}),
        error_messages={'invalid': 'Enter a valid 10-digit account number.'},
    )
