import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Check, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from './components/dialog';
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogAction, AlertDialogCancel, AlertDialogFooter } from './components/alert-dialog';
import { Popover, PopoverContent, PopoverTrigger } from './components/popover';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator } from './components/dropdown-menu';
import { Command, CommandInput, CommandList, CommandEmpty, CommandItem } from './components/command';

// Workspace forms keep their existing server validation, CSRF and review steps.
// Match form actions across modules, excluding public/authentication surfaces.
const FORM_PATH = /^\/(?:inventory|procurement|sales|expenses|finance|orders|runs|batches|users|delivery|commerce|business|assistant|founder|platform|retail)(?:\/[^?#]*)?\/(?:add|new|edit|create|settings|payment-settings|permissions|approve|reject|complete|reverse|recreate|split|quality-check|reconcile|release|sell|sell-online|transfer|return|payments|dispense|use|receive|open|close)\/$/;
const SPECIAL_FORM_PATH = /^\/(?:settings|ledger\/count|inventory\/(?:alert-settings|sample-setup|product-categories|product-use\/(?:market|physical))|sales\/customers\/\d+\/reprice|expenses\/requests\/\d+|commerce\/(?:products\/\d+|notifications\/device|whatsapp)|finance\/reconcile\/\d+|users\/(?:payroll\/addon|plans\/payment(?:\/[^/]+)?|founder\/platform\/(?:business|user)\/\d+(?:\/(?:delete|reset)(?:\/\d+\/(?:restore|delete))?)?))\/$/;
const isFormPath = (path: string) => !['/retail/profile/', '/business/settings/'].includes(path) && (FORM_PATH.test(path) || SPECIAL_FORM_PATH.test(path));
const formDialogUrl = (href: string) => { const url = new URL(href, location.href); url.searchParams.set('ui_dialog', '1'); return url.href; };
const formProfile = (path: string) => {
  if (/^\/finance\/accounts\/(?:add|\d+\/edit)\/$/.test(path)) return { size: 'compact', width: 42, height: 40 };
  if (/^\/procurement\/suppliers\/(?:add|\d+\/edit)\/$/.test(path)) return { size: 'compact', width: 42, height: 32 };
  if (/^\/users\/roles\/(?:add|\d+\/edit)\/$/.test(path)) return { size: 'compact', width: 42, height: 28 };
  if (/^\/(?:orders|runs|procurement\/(?!suppliers)|expenses\/(?:add|\d+\/edit|requests)|sales\/add|inventory\/(?:product\/|market-stock\/|product-use\/)|users\/.*permissions|finance\/books\/)/.test(path)) return { size: 'wide', width: 76, height: 48 };
  return { size: 'standard', width: 56, height: 42 };
};
const PRODUCT_DETAIL_PATH = /^\/inventory\/product\/\d+\/detail\/$/;
const CUSTOMER_ACTIVITY_PATH = /^\/sales\/customers\/\d+\/activity\/$/;
const embedded = window.parent !== window && window.name === 'inprofic-form-dialog';
const postParent = (type: string, extra = {}) => window.parent.postMessage({ source: 'inprofic-ui', type, ...extra }, location.origin);
const notifyNative = (el: Element) => ['input', 'change'].forEach(type => el.dispatchEvent(new Event(type, { bubbles: true })));
const dialogTitleText = (element: Element | null) => {
  if (!element) return '';
  const heading = element.cloneNode(true) as HTMLElement;
  heading.querySelectorAll('[data-form-help-tip],.form-help-tip,[role="tooltip"],[hidden],.sr-only,button,svg').forEach(node => node.remove());
  return (heading.textContent || '').replace(/\s+/g, ' ').trim();
};
// Flatten only the outer page shell; meaningful field groups and notices stay.
function prepareDialogBody(root: HTMLElement) {
  const heading = root.querySelector<HTMLElement>('[data-dialog-title],h1,h2,h3');
  if (heading) {
    const title = dialogTitleText(heading);
    root.querySelectorAll<HTMLElement>('h1,h2,h3').forEach(node => {
      if (dialogTitleText(node) === title) node.dataset.uiDialogDuplicate = '';
    });
    const kicker = heading.previousElementSibling as HTMLElement | null;
    if (kicker?.tagName === 'P' && kicker.classList.contains('uppercase')) kicker.dataset.uiDialogDuplicate = '';
  }
  root.querySelectorAll<HTMLFormElement>('form[method="post" i]').forEach(form => {
    if (form.closest('.workspace-utility-bar') || (form.closest('dialog') && form.closest('dialog') !== root)) return;
    let panel: HTMLElement | null = form;
    while (panel && panel !== root) { panel.dataset.uiFormPanel = ''; panel = panel.parentElement; }
  });
  root.querySelectorAll<HTMLElement>('a,button[type="button"],button:not([type])').forEach(control => {
    if (control.closest('dialog') && control.closest('dialog') !== root) return;
    if (control.closest('[data-ui-portal], [role="combobox"]')) return;
    const label = (control.getAttribute('aria-label') || control.textContent || '').trim();
    const button = control as HTMLButtonElement;
    const isSubmit = control.tagName === 'BUTTON' && !!button.form && button.type === 'submit' && button.form.method !== 'dialog';
    if (!isSubmit && /^(?:cancel|close(?: .*)?|back|return to .*)$/i.test(label)) control.dataset.uiDialogDuplicate = '';
  });
}
let confirmRequest: (message: string, destructive?: boolean, purpose?: string) => Promise<boolean>;
let openForm: (url: string, label: string, trigger: HTMLElement) => void;
let openLegacy: (dialog: HTMLDialogElement) => void;
let closeLegacy: (dialog: HTMLDialogElement, value?: string) => void;
let pendingLegacy: HTMLDialogElement | null = null;

function ConfirmHost() {
  const [request, setRequest] = useState<any>(null);
  useEffect(() => { confirmRequest = (message, destructive = true, purpose) => new Promise(resolve => setRequest({ message, destructive, purpose, resolve })); }, []);
  const finish = (value: boolean) => { request?.resolve(value); setRequest(null); };
  return <AlertDialog open={!!request} onOpenChange={value => { if (!value) finish(false); }}>
    <AlertDialogContent className="ui-confirm" onEscapeKeyDown={() => finish(false)}>
      <AlertDialogTitle>{request?.purpose === 'edit-payment' ? 'Review recorded payments' : request?.destructive ? 'Confirm this action' : 'Leave without saving?'}</AlertDialogTitle>
      <AlertDialogDescription className="ui-dialog-description">{request?.message}</AlertDialogDescription>
      <AlertDialogFooter><AlertDialogCancel data-button-surface onClick={() => finish(false)}>{request?.purpose === 'edit-payment' ? 'Cancel' : 'Keep working'}</AlertDialogCancel><AlertDialogAction data-button-surface className={request?.destructive ? 'ui-danger' : 'ui-button-primary'} onClick={() => finish(true)}>{request?.purpose === 'edit-payment' ? 'Continue editing' : request?.destructive ? 'Confirm' : 'Discard changes'}</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}

function SnapshotMenu({ source }: { source: HTMLElement }) {
  const [, refresh] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => refresh(value => value + 1));
    observer.observe(source, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['disabled'] });
    return () => observer.disconnect();
  }, [source]);
  const commands = [...source.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>('[data-snapshot-commands] button,[data-snapshot-commands] a')];
  const status = source.querySelector('[data-snapshot-status]')?.textContent || '';
  return <div className="ui-snapshot-tools">
    <DropdownMenu>
      <DropdownMenuTrigger asChild><button type="button" className="workspace-icon-button" aria-label={source.dataset.shareLabel || 'Share'}>Share <ChevronDown aria-hidden="true" size={14} /></button></DropdownMenuTrigger>
      <DropdownMenuContent className="ui-menu" align="end" sideOffset={6}>
        {commands.map((command, index) => <DropdownMenuItem key={index} disabled={command.tagName === 'BUTTON' && (command as HTMLButtonElement).disabled} onSelect={() => {
          if (command.hasAttribute('data-snapshot-action')) document.dispatchEvent(new CustomEvent('inprofic:snapshot-action', { detail: { command } }));
          else command.click();
        }}>{command.textContent}</DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
    <span role="status" className="ui-snapshot-feedback">{status}</span>
  </div>;
}

function FormHost() {
  const [model, setModel] = useState<any>(null);
  const [loaded, setLoaded] = useState(false);
  const [shareSource, setShareSource] = useState<HTMLElement | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const preparedDocument = useRef<Document | null>(null);
  const dirty = useRef(false), submitted = useRef(false), changed = useRef(false), stayOpen = useRef(false);
  const close = async (saved = false) => {
    if (!saved && dirty.current && !(await confirmRequest('Your changes have not been saved. Discard them and return to the list?', false))) return;
    const trigger = model?.trigger;
    setModel(null); dirty.current = false;
    requestAnimationFrame(() => trigger?.isConnected && trigger.focus());
    if (saved || changed.current) window.location.reload();
  };
  useEffect(() => {
    openForm = (url, title, trigger) => {
      dirty.current = false; submitted.current = false; changed.current = false;
      stayOpen.current = false;
      preparedDocument.current = null;
      setLoaded(false); setShareSource(null); setModel({ url: formDialogUrl(url), title, trigger, ...formProfile(new URL(url).pathname) });
    };
  }, []);
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (!model || event.origin !== location.origin || event.source !== frame.current?.contentWindow || event.data?.source !== 'inprofic-ui') return;
      if (event.data.type === 'dirty') dirty.current = true;
      if (event.data.type === 'submit') { submitted.current = true; stayOpen.current = !!event.data.stayOpen; }
      if (event.data.type === 'cancel') void close();
      if (event.data.type === 'ready') ready();
    };
    const beforeUnload = (event: BeforeUnloadEvent) => { if (model && dirty.current) event.preventDefault(); };
    window.addEventListener('message', listener); window.addEventListener('beforeunload', beforeUnload);
    return () => { window.removeEventListener('message', listener); window.removeEventListener('beforeunload', beforeUnload); };
  }, [model]);
  const ready = () => {
    try {
      const doc = frame.current!.contentDocument!;
      if (!doc || preparedDocument.current === doc) return;
      preparedDocument.current = doc;
      setShareSource(doc.querySelector<HTMLElement>('[data-snapshot-heading] [data-snapshot-share]'));
      const content = doc.querySelector<HTMLElement>('.workspace-content');
      if (content) prepareDialogBody(content);
      setLoaded(true);
      const url = new URL(frame.current!.contentWindow!.location.href);
      const hasError = !!doc.querySelector('[data-message-level~="error"],.errorlist,.field-has-error,.pa-error,.pa-alert,.expense-field-error,[role="alert"][data-error]');
      const success = !!doc.querySelector('[data-message-level~="success"]');
      // Never treat an expired session or permission page as a successful save.
      const isWorkspace = !!doc.querySelector('[data-ui-workspace]');
      const reviewStep = !!doc.querySelector('[data-ui-review-step]');
      if (submitted.current && reviewStep && !hasError) dirty.current = false;
      if (submitted.current && !reviewStep && !stayOpen.current && isWorkspace && !hasError && (success || url.pathname !== new URL(model.url).pathname)) { void close(true); return; }
      if (success && submitted.current) { changed.current = true; dirty.current = false; }
      submitted.current = false;
      if (hasError) requestAnimationFrame(() => {
        const field = doc.querySelector<HTMLElement>('.field-has-error .ui-combo-trigger,.field-has-error input:not([type=hidden]),.field-has-error textarea,.errorlist');
        field?.focus(); field?.scrollIntoView({ block: 'center' });
      });
      const title = dialogTitleText(doc.querySelector('[data-dialog-title]') || doc.querySelector('.workspace-content h1,.workspace-content h2'));
      if (title) setModel((previous: any) => previous ? { ...previous, title } : previous);
    } catch (_) { /* Keep the dialog available so the user can close a redirected session. */ }
  };
  return <Dialog open={!!model} onOpenChange={value => { if (!value) void close(); }}>
    <DialogContent style={{ "--ui-form-width": `${model?.width || 56}rem`, "--ui-form-height": `${model?.height || 42}rem` } as React.CSSProperties} data-dialog-size={model?.size} className="ui-form-dialog" onOpenAutoFocus={event => { event.preventDefault(); requestAnimationFrame(() => frame.current?.closest<HTMLElement>('[role="dialog"]')?.querySelector<HTMLElement>('.ui-dialog-heading')?.focus()); }} onCloseAutoFocus={event => event.preventDefault()}>
      <div className="ui-dialog-heading" tabIndex={-1}><DialogTitle>{model?.title || 'Edit record'}</DialogTitle><DialogDescription className="sr-only">{model?.url && (CUSTOMER_ACTIVITY_PATH.test(new URL(model.url).pathname) || PRODUCT_DETAIL_PATH.test(new URL(model.url).pathname)) ? "Review activity, costs and special prices. Date filters do not change records." : "Complete the form. Validation and saving follow the same business rules as the full page."}</DialogDescription>
        {loaded && shareSource && <SnapshotMenu source={shareSource} />}
      </div>
      {model && <iframe ref={frame} src={model.url} name="inprofic-form-dialog" title={model.title || 'Record form'} onLoad={ready} className="ui-form-frame" style={{ visibility: loaded ? 'visible' : 'hidden' }} />}
    </DialogContent>
  </Dialog>;
}

function LegacyHost() {
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null);
  const content = useRef<HTMLDivElement>(null), placeholder = useRef<Comment | null>(null), trigger = useRef<HTMLElement | null>(null);
  const finish = (value = '') => {
    if (!dialog) return;
    dialog.returnValue = value; dialog.removeAttribute('open');
    placeholder.current?.replaceWith(dialog); placeholder.current = null;
    dialog.dispatchEvent(new Event('close')); setDialog(null);
    requestAnimationFrame(() => trigger.current?.isConnected && trigger.current.focus());
  };
  useEffect(() => {
    openLegacy = node => {
      if (node.open) return;
      window.dispatchEvent(new Event('inprofic:dialog-opening'));
      trigger.current = document.activeElement as HTMLElement;
      prepareDialogBody(node);
      node.dataset.uiDialogWidth = getComputedStyle(node).maxWidth === 'none' ? '68rem' : getComputedStyle(node).maxWidth;
      placeholder.current = document.createComment('dialog location'); node.before(placeholder.current);
      node.setAttribute('open', ''); setDialog(node);
    };
    if (pendingLegacy) { const node = pendingLegacy; pendingLegacy = null; openLegacy(node); }
  }, []);
  useEffect(() => {
    closeLegacy = (node, value) => { if (node === dialog) finish(value); };
    if (dialog && content.current) content.current.append(dialog);
  }, [dialog]);
  return <Dialog open={!!dialog} onOpenChange={value => { if (!value) { const event = new Event('cancel', { cancelable: true }); if (dialog?.dispatchEvent(event)) finish(); } }}>
    <DialogContent portalContainer={placeholder.current?.parentElement} data-dialog-size={dialog?.dataset.formSize} style={{ "--ui-legacy-width": dialog?.dataset.uiDialogWidth || "68rem", "--ui-form-height": dialog?.dataset.formSize === 'compact' ? '30rem' : '48rem' } as React.CSSProperties} className="ui-legacy-dialog" onOpenAutoFocus={event => { event.preventDefault(); requestAnimationFrame(() => content.current?.closest<HTMLElement>('[role="dialog"]')?.querySelector<HTMLElement>('.ui-dialog-heading')?.focus()); }} onCloseAutoFocus={event => event.preventDefault()}>
      <div className="ui-dialog-heading" tabIndex={-1}><DialogTitle>{dialogTitleText(dialog?.querySelector('h1,h2,h3') || null) || dialog?.getAttribute('aria-label') || 'Details'}</DialogTitle>
        <DialogDescription className="sr-only">Review the details and available actions.</DialogDescription></div>
      <div ref={node => { content.current = node; if (node && dialog) { node.append(dialog); dialog.removeAttribute("aria-hidden"); } }} data-ui-legacy />
    </DialogContent>
  </Dialog>;
}

const selectPopoverInteractions = new WeakMap<Event, HTMLSelectElement>();

function SelectCombo({ select }: { select: HTMLSelectElement }) {
  const [open, setOpen] = useState(false), [revision, refresh] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const options = Array.from(select.options).filter(option => !option.hidden && !option.closest('optgroup')?.disabled);
  const selected = options.filter(option => option.selected);
  const label = select.getAttribute('aria-label') || document.querySelector(`label[for="${CSS.escape(select.id)}"]`)?.textContent?.trim() || select.name.replaceAll('_', ' ') || 'Choose an option';
  useEffect(() => {
    const update = () => refresh(value => value + 1);
    const invalid = (event: Event) => { event.preventDefault(); trigger.current?.focus(); trigger.current?.setAttribute('aria-invalid', 'true'); };
    const labelClick = (event: Event) => { if ((event.target as Element).closest('button,a,input')) return; event.preventDefault(); trigger.current?.focus(); };
    const labels = Array.from(select.labels || []); labels.forEach(label => label.addEventListener('click', labelClick));
    const observer = new MutationObserver(update); observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'selected', 'label', 'required'] });
    select.addEventListener('change', update); select.addEventListener('ms-refresh', update); select.addEventListener('invalid', invalid);
    const reset = () => queueMicrotask(update); select.form?.addEventListener('reset', reset);
    // Existing page scripts often assign select.value without dispatching an
    // event. Refresh on focus/open as well as option mutations.
    const focus = () => update(); window.addEventListener('focus', focus);
    return () => { labels.forEach(label => label.removeEventListener('click', labelClick)); observer.disconnect(); select.removeEventListener('change', update); select.removeEventListener('ms-refresh', update); select.removeEventListener('invalid', invalid); select.form?.removeEventListener('reset', reset); window.removeEventListener('focus', focus); };
  }, [select]);
  const choose = (option: HTMLOptionElement) => {
    if (option.disabled || select.disabled) return;
    if (select.multiple) option.selected = !option.selected; else select.value = option.value;
    notifyNative(select); refresh(value => value + 1); if (!select.multiple) setOpen(false);
  };
  return <Popover open={open} onOpenChange={value => { refresh(n => n + 1); setOpen(value); }}>
    <PopoverTrigger asChild><button ref={trigger} type="button" className="ui-combo-trigger" role="combobox" aria-expanded={open} aria-label={label} aria-required={select.required} aria-invalid={select.getAttribute('aria-invalid') || undefined} disabled={select.disabled} onFocus={() => refresh(n => n + 1)}>
      <span>{select.multiple ? (selected.length ? selected.map(option => option.label).join(', ') : select.dataset.placeholder || 'Choose options') : selected[0]?.label || 'Choose an option'}</span><ChevronsUpDown aria-hidden="true" size={15} />
    </button></PopoverTrigger>
    <PopoverContent onPointerDownCapture={event => selectPopoverInteractions.set(event.nativeEvent, select)} onFocusCapture={event => selectPopoverInteractions.set(event.nativeEvent, select)} aria-label={`${label} options`} className="ui-combo-popover" align="start">
      <Command key={revision} loop><CommandInput placeholder="Search options…" aria-label={`Search ${label}`} /><CommandList><CommandEmpty>No matching options.</CommandEmpty>
        {options.map((option, index) => <CommandItem key={index} value={`${index} ${option.label}`} disabled={option.disabled} onSelect={() => choose(option)} aria-selected={option.selected}><Check aria-hidden="true" size={14} style={{ visibility: option.selected ? 'visible' : 'hidden' }} /><span>{option.label || 'None'}</span></CommandItem>)}
      </CommandList></Command>
    </PopoverContent>
  </Popover>;
}

function PublicNavigation() {
  return <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="workspace-icon-button" aria-label="Explore INPROFIC">Explore</button></DropdownMenuTrigger><DropdownMenuContent className="ui-menu" align="end">{[['#about', 'About INPROFIC'], ['#interactive-demo', 'Interactive walkthrough'], ['#capabilities', 'Connected modules'], ['#workflows', 'Stock to sale'], ['#plans', 'Plans and features'], ['#faq', 'Questions and answers']].map(([href, label]) => <DropdownMenuItem key={href} asChild><a href={href}>{label}</a></DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu>;
}

function AppearancePopover({ panel, button }: { panel: HTMLElement; button: HTMLElement }) {
  const placeholder = useRef<Comment | null>(null);
  useEffect(() => {
    panel.removeAttribute('data-appearance-panel');
    panel.dataset.uiAppearanceBody = '';
    placeholder.current = document.createComment('appearance-panel');
    panel.before(placeholder.current);
    return () => { placeholder.current?.replaceWith(panel); panel.hidden = true; };
  }, [panel]);
  const attach = (node: HTMLDivElement | null) => {
    if (node) { node.append(panel); panel.hidden = false; }
    else { placeholder.current?.after(panel); panel.hidden = true; }
  };
  return <Popover><PopoverTrigger asChild><button type="button" className="workspace-icon-button" aria-label="Appearance"><span aria-hidden="true" dangerouslySetInnerHTML={{ __html: button.innerHTML }} /></button></PopoverTrigger>
    <PopoverContent className="ui-appearance-content" align="end"><div ref={attach} /></PopoverContent>
  </Popover>;
}

// Keep Django-owned filter fields intact while giving compact cards the same
// focus, Escape, outside-click and viewport collision behavior as other menus.
function FilterPopover({ panel, button }: { panel: HTMLElement; button: HTMLElement }) {
  const [open, setOpen] = useState(false);
  const placeholder = useRef<Comment | null>(null);
  useEffect(() => {
    placeholder.current = document.createComment('filter-panel');
    panel.before(placeholder.current);
    return () => { placeholder.current?.replaceWith(panel); panel.hidden = true; };
  }, [panel]);
  const attach = React.useCallback((node: HTMLDivElement | null) => {
    if (node) { node.append(panel); panel.hidden = false; }
    else { placeholder.current?.after(panel); panel.hidden = true; }
  }, [panel]);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><button type="button" className="workspace-icon-button" aria-label={button.getAttribute('aria-label') || 'Choose period'}><span aria-hidden="true" dangerouslySetInnerHTML={{ __html: button.innerHTML }} /></button></PopoverTrigger>
    <PopoverContent className="ui-filter-content" align="end" collisionPadding={12} aria-label={button.getAttribute('aria-label') || 'Choose period'} onInteractOutside={event => {
      // Native-field islands have separate React roots. Their portalled lists
      // are logically inside this filter even though Radix sees them outside.
      // Radix defers pointer dismissal until click. Remember the original
      // event's owner even after selecting an option unmounts its list.
      const original = event.detail.originalEvent;
      const target = original.target;
      const owner = selectPopoverInteractions.get(original);
      if ((target instanceof Node && panel.contains(target)) || (owner && panel.contains(owner))) event.preventDefault();
    }}>
      <div ref={attach} />
      <button type="button" className="workspace-button mt-3 w-full" onClick={() => setOpen(false)}>Done</button>
    </PopoverContent>
  </Popover>;
}

const selectRoots = new Map<HTMLSelectElement, { root: ReturnType<typeof createRoot>, mount: HTMLElement }>();
function enhanceSelections(root: ParentNode = document) {
  root.querySelectorAll<HTMLSelectElement>('select:not([data-native-select])').forEach(select => {
    const existing = selectRoots.get(select);
    if (existing) {
      // Form-experience may wrap the native field after this island mounted.
      // Keep its visible control inside that same icon/validation shell.
      if (select.nextElementSibling !== existing.mount) select.after(existing.mount);
      return;
    }
    if (select.closest('template,[hidden]') || select.style.display === 'none' || select.hidden || select.dataset.msBound || !select.options.length) return;
    // cloneNode copies data/classes but not React roots. Remove cloned visual
    // widgets before wiring the new formset row to its own native field.
    const clonedWidget = select.nextElementSibling;
    if (clonedWidget?.matches('[data-ui-combo-root]')) clonedWidget.remove();
    select.classList.add('ui-native-select'); select.setAttribute('aria-hidden', 'true'); select.tabIndex = -1;
    const mount = document.createElement('span'); mount.dataset.uiComboRoot = ''; select.after(mount);
    const reactRoot = createRoot(mount); selectRoots.set(select, { root: reactRoot, mount }); reactRoot.render(<SelectCombo select={select} />);
  });
  selectRoots.forEach((value, select) => { if (!select.isConnected) { value.root.unmount(); value.mount.remove(); selectRoots.delete(select); } });
}

// List workspaces also contain inline editors (Books, Tax, mailing, delivery).
// Keep their actual forms and listeners; expose them through the same dialog.
const inlineForms = new WeakSet<HTMLFormElement>();
const inlineRetryKey = 'inprofic:inline-form-retry';
let inlineRetry: { path: string, index: number, at: number } | null | undefined;
function enhanceInlineForms() {
  if (embedded || !document.querySelector('.desktop-sidebar') || isFormPath(location.pathname)) return;
  if (inlineRetry === undefined) {
    try { inlineRetry = JSON.parse(sessionStorage.getItem(inlineRetryKey) || 'null'); sessionStorage.removeItem(inlineRetryKey); } catch (_) { inlineRetry = null; }
  }
  document.querySelectorAll<HTMLFormElement>('.workspace-content form[method="post" i]').forEach((form, index) => {
    if (inlineForms.has(form) || form.closest('dialog,[role="dialog"],.workspace-utility-bar,[data-no-dialog],[data-standalone-profile]')) return;
    const fields = [...form.elements].filter(field => field.matches('input:not([type="hidden"]):not([type="submit"]):not([type="button"]),select,textarea')) as HTMLInputElement[];
    if (!fields.some(field => !field.matches(':disabled') && !field.readOnly && !!field.getClientRects().length)) return;
    const submit = form.querySelector<HTMLButtonElement>('button:not([type]),button[type="submit"],input[type="submit"]');
    if (!submit || submit.matches(':disabled')) return;
    inlineForms.add(form);
    const action = dialogTitleText(submit) || submit.value || 'Open form';
    const label = /^(?:add|save|create|update|apply)$/i.test(action)
      ? dialogTitleText(form.querySelector('h1,h2,h3')) || action
      : action.replace(/^save /i, 'Edit ');
    const external = fields.filter(field => !form.contains(field));
    if (external.length) {
      // Production approvals associate flexible amounts with form="…". Keep
      // their complete stock/review context together in an embedded workspace.
      const groups = form.id ? [...document.querySelectorAll<HTMLElement>(`[data-form-fields-for="${CSS.escape(form.id)}"]`)] : [];
      const entry = groups.find(group => group.contains(form)) || form;
      const trigger = document.createElement('button');
      trigger.type = 'button'; trigger.className = 'workspace-action ui-inline-form-trigger'; trigger.textContent = label;
      trigger.setAttribute('aria-haspopup', 'dialog'); entry.before(trigger);
      const hide = (node: HTMLElement) => { node.hidden = true; node.dataset.uiInlineSource = ''; };
      hide(form); groups.forEach(hide);
      external.filter(field => !groups.some(group => group.contains(field))).forEach(field => hide(field.closest<HTMLElement>('label,.inprofic-control-wrap') || field));
      trigger.addEventListener('click', () => openForm?.(location.href, label, trigger));
      return;
    }
    const dialog = document.createElement('dialog');
    const title = document.createElement('h2'); title.textContent = label;
    const wide = form.dataset.formSize === 'wide' || !!form.querySelector('table,[data-formset]') || fields.length > 8;
    dialog.style.maxWidth = wide ? '76rem' : fields.length > 4 ? '56rem' : '42rem';
    dialog.dataset.formSize = wide ? 'wide' : fields.length > 4 ? 'standard' : 'compact';
    dialog.dataset.inlineFormDialog = '';
    const trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'workspace-action ui-inline-form-trigger'; trigger.textContent = label;
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.addEventListener('click', () => dialog.showModal());
    // Row editors must continue to show the record's current name/value.
    if (form.closest('td') && fields.length === 1 && fields[0].value) {
      const value = document.createElement('span'); value.textContent = fields[0].value;
      value.className = 'ui-inline-form-value'; form.before(value);
      trigger.setAttribute('aria-label', `${label}: ${fields[0].value}`);
    }
    form.before(trigger, dialog); dialog.append(title, form);
    form.addEventListener('submit', event => queueMicrotask(() => {
      if (event.defaultPrevented) return;
      // Remember only the entry point, never passwords, payment details or files.
      try { sessionStorage.setItem(inlineRetryKey, JSON.stringify({ path: location.pathname, index, at: Date.now() })); } catch (_) { /* Field errors still reopen the editor. */ }
    }));
    const errors = [...document.querySelectorAll<HTMLElement>('[data-message-level~="error"]')];
    const retry = inlineRetry?.path === location.pathname && inlineRetry.index === index && Date.now() - inlineRetry.at < 300000 && errors.length;
    if (retry || form.querySelector('.errorlist,.field-has-error,[aria-invalid="true"]')) {
      errors.forEach(error => {
        const copy = error.cloneNode(true) as HTMLElement;
        copy.removeAttribute('id'); copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
        copy.setAttribute('role', 'alert'); form.before(copy);
      });
      requestAnimationFrame(() => dialog.showModal());
    }
  });
}

function initializeSidebar() {
  const sidebarToggle = document.querySelector<HTMLButtonElement>('#sidebar-toggle');
  if (sidebarToggle) {
    const root = document.documentElement;
    const categories = [...document.querySelectorAll<HTMLDetailsElement>('.desktop-sidebar [data-nav-category]')];
    const expandedCategories = new Set(categories.filter(category => category.open));
    const apply = () => {
      const collapsed = root.dataset.sidebar === 'collapsed';
      sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
      sidebarToggle.setAttribute('aria-label', collapsed ? 'Expand Panel' : 'Collapse Panel');
      sidebarToggle.title = collapsed ? 'Expand Panel' : 'Collapse Panel';
      categories.forEach(category => {
        // Closed native details hide descendants from keyboard and screen readers.
        // Open all groups in the icon rail and restore their state on expansion.
        if (collapsed) { category.removeAttribute('name'); category.open = true; }
        else { category.open = expandedCategories.has(category); category.name = 'desktop-sidebar-nav'; }
      });
    };
    document.querySelectorAll<HTMLElement>('.desktop-sidebar nav a,.desktop-sidebar nav button').forEach(link => {
      const label = link.querySelector('.nav-label')?.textContent?.trim();
      if (label) { link.title = label; link.setAttribute('aria-label', label); }
    });
    sidebarToggle.addEventListener('click', () => {
      if (root.dataset.sidebar !== 'collapsed') { expandedCategories.clear(); categories.filter(category => category.open).forEach(category => expandedCategories.add(category)); }
      root.dataset.sidebar = root.dataset.sidebar === 'collapsed' ? 'expanded' : 'collapsed';
      try { localStorage.setItem('inprofic:sidebar', root.dataset.sidebar); } catch (_) { /* Navigation remains usable without storage. */ }
      apply();
    });
    apply();
  }
}

function boot() {
  initializeSidebar();
  const mount = document.createElement('div'); mount.dataset.uiRoot = ''; document.body.append(mount);
  createRoot(mount).render(<><ConfirmHost />{!embedded && <><FormHost /><LegacyHost /></>}</>);
  document.querySelectorAll<HTMLElement>('[data-ui-snapshot-menu]').forEach(node => {
    const source = node.closest<HTMLElement>('[data-snapshot-share]');
    if (source) createRoot(node).render(<SnapshotMenu source={source} />);
  });
  document.querySelectorAll('[data-ui-site-menu]').forEach(node => createRoot(node).render(<PublicNavigation />));
  document.querySelectorAll<HTMLElement>('[data-appearance-root]').forEach(node => {
    const button = node.querySelector<HTMLElement>('[data-appearance-toggle]'), panel = node.querySelector<HTMLElement>('[data-appearance-panel]');
    if (!button || !panel) return;
    const slot = document.createElement('span'); node.append(slot); button.style.display = 'none';
    createRoot(slot).render(<AppearancePopover panel={panel} button={button} />);
  });
  document.querySelectorAll<HTMLElement>('[data-ui-filter-root]').forEach(node => {
    const button = node.querySelector<HTMLElement>('[data-ui-filter-toggle]');
    const panel = node.querySelector<HTMLElement>('[data-ui-filter-panel]');
    if (!button || !panel) return;
    const slot = document.createElement('span'); node.append(slot); button.style.display = 'none';
    createRoot(slot).render(<FilterPopover panel={panel} button={button} />);
  });
  enhanceSelections();
  enhanceInlineForms();
  let queued = false;
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; enhanceSelections(); enhanceInlineForms(); bridgeDialogs(); }); } }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'style'] });
  const approved = new WeakSet<HTMLFormElement>();
  document.addEventListener('submit', async event => {
    const form = event.target as HTMLFormElement;
    if (approved.delete(form)) return;
    const code = form.getAttribute('onsubmit') || '';
    const match = code.match(/^\s*return\s+confirm\((['"])([\s\S]*)\1\);?\s*$/);
    if (!match || !confirmRequest) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const message = match[2].replace(/\\(['"\\nrt])/g, (_, c) => ({ n: '\n', r: '\r', t: '\t' }[c] || c));
    if (await confirmRequest(message)) { form.onsubmit = null; form.removeAttribute('onsubmit'); approved.add(form); form.requestSubmit((event as SubmitEvent).submitter as HTMLElement | null); }
  }, true);
  document.addEventListener('click', async event => {
    const mouse = event as MouseEvent, link = (event.target as Element).closest<HTMLAnchorElement>('a[href]');
    if (!link || event.defaultPrevented || mouse.button || mouse.metaKey || mouse.ctrlKey || mouse.shiftKey || mouse.altKey || link.target || link.hasAttribute('download') || link.hasAttribute('data-no-dialog')) return;
    const url = new URL(link.href, location.href);
    if (embedded) {
      if (/^(cancel|back|return)\b/i.test(link.textContent?.trim() || '')) { event.preventDefault(); postParent('cancel'); }
      return;
    }
    if (url.origin !== location.origin || !(link.hasAttribute('data-form-dialog') || isFormPath(url.pathname) || CUSTOMER_ACTIVITY_PATH.test(url.pathname) || PRODUCT_DETAIL_PATH.test(url.pathname)) || !openForm) return;
    event.preventDefault();
    if (link.dataset.confirm && !(await confirmRequest(link.dataset.confirm, false, 'edit-payment'))) return;
    openForm(url.href, link.getAttribute('aria-label') || dialogTitleText(link).replace(/^\+\s*/, '') || 'Record form', link);
  });
  if (embedded) {
    document.documentElement.dataset.uiEmbedded = 'true';
    const content = document.querySelector<HTMLElement>('.workspace-content');
    if (content) prepareDialogBody(content);
    // Embedded forms share the active palette, including changes made while open.
    const syncAppearance = () => {
      const source = window.parent.document.documentElement, root = document.documentElement;
      root.toggleAttribute('data-theme-changing', source.hasAttribute('data-theme-changing'));
      for (const name of ['theme', 'themePreference', 'accentPreference', 'backgroundPreference']) {
        if (source.dataset[name]) root.dataset[name] = source.dataset[name];
      }
      const styles = window.parent.getComputedStyle(source);
      for (const name of styles) if (name.startsWith('--ui-') || name.startsWith('--tenant-')) root.style.setProperty(name, styles.getPropertyValue(name));
    };
    syncAppearance();
    new MutationObserver(syncAppearance).observe(window.parent.document.documentElement, { attributes: true });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector('dialog[open],[role=dialog][data-state=open],[role=alertdialog]')) { event.preventDefault(); postParent('cancel'); }
    });
    const markDirty = (event: Event) => {
      if (!(event.target as HTMLElement)?.closest('form[method="get"],[data-readonly-detail]')) postParent('dirty');
    };
    document.addEventListener('input', markDirty); document.addEventListener('change', markDirty);
    document.addEventListener('submit', event => { queueMicrotask(() => { if (!event.defaultPrevented) postParent('submit', { stayOpen: ((event as SubmitEvent).submitter?.getAttribute('name') === 'save_add_new' || (event as SubmitEvent).submitter?.hasAttribute('data-stay-open')) }); }); });
  }
  bridgeDialogs(); document.documentElement.dataset.uiReady = 'true';
  if (embedded) {
    // DOM readiness includes the form's deferred scripts; don't wait for logos,
    // fonts or unrelated page assets before presenting an interactive form.
    if (document.readyState === 'complete') postParent('ready');
    else document.addEventListener('DOMContentLoaded', () => postParent('ready'), { once: true });
  }
}
function bridgeDialogs() {
  if (embedded) return;
  document.querySelectorAll<HTMLDialogElement>('dialog:not([data-native-dialog])').forEach(dialog => {
    if (dialog.dataset.uiDialog) return; dialog.dataset.uiDialog = 'true';
    Object.defineProperty(dialog, 'showModal', { configurable: true, value: () => { if (openLegacy) openLegacy(dialog); else pendingLegacy = dialog; } });
    Object.defineProperty(dialog, 'close', { configurable: true, value: (value = '') => closeLegacy?.(dialog, value) });
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
