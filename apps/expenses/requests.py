"""Daily general-use requests remain outside spending until approved."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from accounts.services import is_business_admin, user_has_permission
from core import ledger
from core.models import CashAccount, FinancialTransaction
from core.services import audit, record_cash
from core.share_text import render_lines
from core.snapshot_images import render_report_snapshot, snapshot_response
from .forms import INPUT_CLS
from .models import Expense, ExpenseRequest, ExpenseRequestLine


def require_access(request, action='view'):
    if not user_has_permission(request.user, request.business, 'expenses', action):
        raise PermissionDenied


class RequestForm(forms.ModelForm):
    class Meta:
        model = ExpenseRequest
        fields = ['date','notes']
        widgets = {'date':forms.DateInput(attrs={'type':'date'}), 'notes':forms.Textarea(attrs={'rows':2})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values(): field.widget.attrs['class']=INPUT_CLS

    def clean_date(self):
        day=self.cleaned_data['date']
        ledger.check_scope_date(day)
        if day > timezone.localdate():
            raise forms.ValidationError('The expense date cannot be in the future.')
        return day


class RequestLineForm(forms.ModelForm):
    class Meta:
        model=ExpenseRequestLine
        fields=['category','description','amount','vendor','payment_status','payment_method','account','notes','approval_reason']
        widgets={'notes':forms.Textarea(attrs={'rows':1}),'approval_reason':forms.Textarea(attrs={'rows':2})}

    def __init__(self,*args,business,require_reason=False,**kwargs):
        self.require_reason=require_reason
        super().__init__(*args,**kwargs)
        self.fields['account'].queryset=CashAccount.objects.filter(business=business,active=True)
        for field in self.fields.values(): field.widget.attrs['class']=INPUT_CLS
        self.fields['amount'].widget.attrs.update(min='0.01',step='0.01')
        self.fields['approval_reason'].label='Reason for change or removal'
        self.fields['approval_reason'].help_text='Required at approval when an amount changes or an item is removed.'

    def clean(self):
        data=super().clean()
        if data.get('id') and data['id'].pk != self.instance.pk:
            self.add_error(None,'This expense item does not belong to this request.')
        if data.get('DELETE') and not self.instance.pk: return data
        if self.require_reason and (data.get('DELETE') or data.get('amount') != self.instance.requested_amount) and not (data.get('approval_reason') or '').strip():
            self.add_error('approval_reason','Give a reason for changing or removing this expense.')
        if data.get('DELETE'): return data
        if data.get('amount') is not None and data['amount'] <= 0:
            self.add_error('amount','Enter an amount greater than zero.')
        if data.get('payment_status') == 'paid' and not data.get('account'):
            self.add_error('account','Select the account used for this payment.')
        return data


class RequestLines(BaseInlineFormSet):
    def clean(self):
        super().clean()
        expected=set(self.get_queryset().values_list('pk',flat=True))
        submitted={getattr(getattr(row,'cleaned_data',{}).get('id'),'pk',None) for row in self.initial_forms}
        if expected != submitted:
            raise forms.ValidationError('The request items changed. Reload the request before saving.')
        # Django excludes deleted forms from is_valid(); removal reasons must
        # therefore also be checked at formset level before approval.
        for row in self.forms:
            if row.require_reason and row.instance.pk and getattr(row,'cleaned_data',{}).get('DELETE') and not (row.data.get(row.add_prefix('approval_reason')) or '').strip():
                raise forms.ValidationError(f'Give a reason for removing {row.instance.description}.')


Lines=inlineformset_factory(ExpenseRequest,ExpenseRequestLine,form=RequestLineForm,formset=RequestLines,
    fields=RequestLineForm.Meta.fields,extra=1,can_delete=True,max_num=100,validate_max=True,absolute_max=120)


def request_share(request, obj):
    symbol=request.business.currency_symbol
    money=lambda value:f'{symbol}{value:,.2f}'
    lines=[f'*General-use expense request · {obj.date:%d %b %Y}*',request.business.name,f'Status: {obj.get_status_display()}','']
    rows=[]
    for line in obj.lines.all():
        if line.removed: continue
        details=' · '.join(value for value in [line.get_category_display(),line.vendor,line.get_payment_status_display(),('Requested: '+money(line.requested_amount)) if line.amount != line.requested_amount else '',('Reason: '+line.approval_reason) if line.approval_reason else ''] if value)
        lines += [f'• {line.description}: {money(line.amount)}',f'  {details}']
        rows.append({'label':line.description,'detail':details,'value':money(line.amount)})
    lines += ['',f'*Total: {money(obj.total)}*','', 'Open INPROFIC to review (sign-in required):', request.build_absolute_uri(reverse('expense_request_detail',args=[obj.pk]))]
    return {'expense_request_text':render_lines(lines),'expense_request_image':reverse('expense_request_image',args=[obj.pk]),'request_snapshot_rows':rows}


@login_required
@transaction.atomic
def request_form(request, pk=None):
    require_access(request,'edit' if request.method=='POST' or pk is None else 'view')
    obj=get_object_or_404(ExpenseRequest.objects.select_for_update().filter(business=request.business),pk=pk) if pk else ExpenseRequest(business=request.business)
    original_date=obj.date if obj.pk else None
    pending=not obj.pk or obj.status=='pending'
    form=RequestForm(request.POST or None,instance=obj,initial={'date':ledger.default_entry_date()} if not obj.pk else None)
    formset=Lines(request.POST or None,instance=obj,prefix='lines',queryset=ExpenseRequestLine.objects.filter(request=obj,removed=False) if obj.pk else ExpenseRequestLine.objects.none(),form_kwargs={'business':request.business,'require_reason':request.POST.get('action')=='approve'})
    if obj.pk: formset.extra=0
    admin=is_business_admin(request.user,request.business)
    if request.method=='POST':
        if not pending: raise PermissionDenied('This request has already been reviewed.')
        action=request.POST.get('action','save')
        if action in ('approve','decline') and not admin: raise PermissionDenied('A business administrator must approve or remove requests.')
        if action=='decline' and obj.pk:
            obj.status='declined';obj.save(update_fields=['status','updated_at'])
            audit(request.business,request.user,'decline',obj,'Daily expense request removed; no spending recorded.')
            return redirect('procurement_list')
        valid=form.is_valid()
        valid=formset.is_valid() and valid
        active=[line for line in formset.forms if getattr(line,'cleaned_data',None) and not line.cleaned_data.get('DELETE') and (line.instance.pk or line.has_changed())]
        if valid and not active:
            form.add_error(None,'Keep at least one expense, or remove the whole request.')
            valid=False
        if valid:
            day=form.cleaned_data['date']
            if obj.pk and day != original_date:
                form.add_error('date','Keep the date of this daily request. Create another request for a different day.')
            else:
                # The database constraint serializes concurrent additions to one pending day.
                target=obj
                if not obj.pk:
                    target,_=ExpenseRequest.objects.get_or_create(business=request.business,date=day,status='pending',defaults={'created_by':request.user})
                    target=ExpenseRequest.objects.select_for_update().get(pk=target.pk,business=request.business)
                target.notes=form.cleaned_data['notes']; target.save()
                for row in formset.forms:
                    data=getattr(row,'cleaned_data',{})
                    if not data or (not row.instance.pk and not row.has_changed()): continue
                    if data.get('DELETE'):
                        if row.instance.pk:
                            row.instance.removed=True;row.instance.approval_reason=data.get('approval_reason','');row.instance.save(update_fields=['removed','approval_reason','updated_at'])
                            audit(request.business,request.user,'remove',target,'Expense request line removed.',{'line':row.instance.pk,'description':row.instance.description,'amount':str(row.instance.amount),'reason':row.instance.approval_reason})
                        continue
                    line=row.save(commit=False);line.request=target
                    if not line.pk: line.requested_amount=line.amount
                    line.save()
                if action=='approve':
                    for line in target.lines.filter(removed=False).select_related('account'):
                        expense=Expense.objects.create(business=request.business,created_by=request.user,date=target.date,
                            category=line.category,description=line.description,amount=line.amount,vendor=line.vendor,notes=line.notes,
                            payment_status=line.payment_status,payment_method=line.payment_method,account=line.account if line.payment_status=='paid' else None)
                        line.expense=expense;line.save(update_fields=['expense','updated_at'])
                        if expense.payment_status=='paid':
                            record_cash(request.business,request.user,date=expense.date,amount=expense.amount,
                                transaction_type=FinancialTransaction.OUTFLOW,category=expense.get_category_display(),description=expense.description,
                                payment_method=expense.payment_method,account=expense.account,reference=f'EXP-{expense.pk}')
                        audit(request.business,request.user,'create',expense,'Expense recorded from approved request.',{'request':target.pk,'line':line.pk,'requested_amount':str(line.requested_amount),'approved_amount':str(line.amount),'reason':line.approval_reason})
                    target.status='approved';target.approved_by=request.user;target.approved_at=timezone.now();target.save()
                audit(request.business,request.user,'approve' if action=='approve' else 'update',target,'Daily general-use expense request '+('approved.' if action=='approve' else 'saved.'),{'total':str(target.total),'date':str(target.date)})
                messages.success(request,'Expenses approved and recorded.' if action=='approve' else 'Expense request saved for approval.')
                return redirect('expense_request_detail',pk=target.pk)
    editable=user_has_permission(request.user,request.business,'expenses','edit')
    if not pending or not editable:
        for field in form.fields.values(): field.disabled=True
        for row in formset:
            for field in row.fields.values(): field.disabled=True
    return render(request,'expenses/request_form.html',{'expense_request':obj,'form':form,'formset':formset,'pending':pending,'can_approve':admin,'can_edit':editable,**(request_share(request,obj) if obj.pk else {})})


@login_required
@require_GET
def request_image(request,pk):
    require_access(request)
    obj=get_object_or_404(ExpenseRequest.objects.filter(business=request.business).prefetch_related('lines'),pk=pk)
    data=request_share(request,obj)
    image=render_report_snapshot(business=request.business,title='General-use expense request',subtitle=f'{obj.date:%d %b %Y} · {obj.get_status_display()}',rows=data['request_snapshot_rows'],
        total_label='Total',total=f'{request.business.currency_symbol}{obj.total:,.2f}',footer='Sign-in required. Open INPROFIC to review.',url=request.build_absolute_uri(reverse('expense_request_detail',args=[obj.pk])),link_heading='Review expense request')
    return snapshot_response(image,filename=f'expense-request-{obj.pk}')
