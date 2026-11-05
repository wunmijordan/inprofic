import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Check, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from './components/dialog';
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogAction, AlertDialogCancel, AlertDialogFooter } from './components/alert-dialog';
import { Popover, PopoverContent, PopoverTrigger } from './components/popover';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator } from './components/dropdown-menu';
import { Command, CommandInput, CommandList, CommandEmpty, CommandItem } from './components/command';

// The server remains the authorization and save boundary. Only contained edits
// belong here. Procurement approval retains its full review inside the dialog.
const FORM_PATH = /^\/(?:inventory\/(?:raw-material|product)\/(?:add|\d+\/edit)|procurement\/(?:suppliers\/)?(?:add|\d+\/(?:edit|approve))|sales\/(?:customers\/)?(?:add|\d+\/edit)|expenses\/(?:add|\d+\/edit|payments\/add)|finance\/(?:accounts\/(?:add|\d+\/edit)|(?:supplier-payment|customer-payment|expense-payment|stock-adjustment)\/add)|(?:orders|runs)\/(?:add|\d+\/edit))\/$/;
const embedded = window.parent !== window && window.name === 'inprofic-form-dialog';
const postParent = (type: string, extra = {}) => window.parent.postMessage({ source: 'inprofic-ui', type, ...extra }, location.origin);
const notifyNative = (el: Element) => ['input','change'].forEach(type => el.dispatchEvent(new Event(type, { bubbles: true })));
let confirmRequest: (message: string, destructive?: boolean, purpose?: string) => Promise<boolean>;
let openForm: (url: string, label: string, trigger: HTMLElement) => void;
let openLegacy: (dialog: HTMLDialogElement) => void;
let closeLegacy: (dialog: HTMLDialogElement, value?: string) => void;

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

function SnapshotMenu({source}: {source: HTMLElement}) {
  const [, refresh] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => refresh(value => value + 1));
    observer.observe(source, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:['disabled']});
    return () => observer.disconnect();
  }, [source]);
  const commands = [...source.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>('[data-snapshot-commands] button,[data-snapshot-commands] a')];
  const status = source.querySelector('[data-snapshot-status]')?.textContent || '';
  return <div className="ui-snapshot-tools">
    <DropdownMenu>
      <DropdownMenuTrigger asChild><button type="button" className="workspace-icon-button" aria-label={source.dataset.shareLabel || 'Share'}>Share <ChevronDown aria-hidden="true" size={14}/></button></DropdownMenuTrigger>
      <DropdownMenuContent className="ui-menu" align="end" sideOffset={6}>
        {commands.map((command, index) => <DropdownMenuItem key={index} disabled={command.tagName === 'BUTTON' && (command as HTMLButtonElement).disabled} onSelect={() => {
          if (command.hasAttribute('data-snapshot-action')) document.dispatchEvent(new CustomEvent('inprofic:snapshot-action', {detail:{command}}));
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
  const sizing = useRef<ResizeObserver | null>(null);
  useEffect(() => () => sizing.current?.disconnect(), []);
  const dirty = useRef(false), submitted = useRef(false), changed = useRef(false), stayOpen = useRef(false);
  const close = async (saved = false) => {
    if (!saved && dirty.current && !(await confirmRequest('Your changes have not been saved. Discard them and return to the list?', false))) return;
    const trigger = model?.trigger;
    sizing.current?.disconnect();
    setModel(null); dirty.current = false;
    requestAnimationFrame(() => trigger?.isConnected && trigger.focus());
    if (saved || changed.current) window.location.reload();
  };
  useEffect(() => {
    openForm = (url, title, trigger) => {
      dirty.current = false; submitted.current = false; changed.current = false;
      const compact = /^\/(?:finance\/accounts|procurement\/suppliers)\//.test(new URL(url).pathname);
      sizing.current?.disconnect();
      setLoaded(false); setShareSource(null); setModel({ url, title, trigger, compact, width: compact ? 42 : 56 });
    };
  }, []);
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (!model || event.origin !== location.origin || event.source !== frame.current?.contentWindow || event.data?.source !== 'inprofic-ui') return;
      if (event.data.type === 'dirty') dirty.current = true;
      if (event.data.type === 'submit') { submitted.current = true; stayOpen.current = !!event.data.stayOpen; }
      if (event.data.type === 'cancel') void close();
    };
    const beforeUnload = (event: BeforeUnloadEvent) => { if (model && dirty.current) event.preventDefault(); };
    window.addEventListener('message', listener); window.addEventListener('beforeunload', beforeUnload);
    return () => { window.removeEventListener('message', listener); window.removeEventListener('beforeunload', beforeUnload); };
  }, [model]);
  const ready = () => {
    setLoaded(true);
    try {
      const doc = frame.current!.contentDocument!;
      setShareSource(doc.querySelector<HTMLElement>('[data-snapshot-heading] [data-snapshot-share]'));
      const form = doc.querySelector<HTMLFormElement>('.workspace-content form[method="post"]');
      if (form) {
        // Mark the enclosing page panel, never interior grouped cards.
        let panel: HTMLElement | null = form;
        const boundary = form.closest('.workspace-content,[data-ui-workspace]');
        while (panel && panel !== boundary) {
          const style = doc.defaultView!.getComputedStyle(panel);
          if (style.borderStyle !== 'none' || style.boxShadow !== 'none') panel.dataset.uiFormPanel = '';
          panel = panel.parentElement;
        }
      }
      sizing.current?.disconnect();
      const content = doc.querySelector<HTMLElement>('.workspace-content');
      const dialog = frame.current?.closest<HTMLElement>('.ui-form-dialog');
      if (content && dialog) {
        const heading = dialog.querySelector<HTMLElement>('.ui-dialog-heading');
        const measure = () => {
          const main = content.closest('main');
          const style = main ? doc.defaultView!.getComputedStyle(main) : null;
          const padding = style ? parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) : 32;
          const height = Math.ceil(content.getBoundingClientRect().height + padding + (heading?.getBoundingClientRect().height || 64) + 2);
          dialog.style.setProperty('--ui-form-height', `${height}px`);
        };
        // Tables and item grids need room; simple record forms stay compact.
        const wide = !!content.querySelector('table,[data-formset]') || /^\/(?:orders|runs|procurement)\/(?:add|\d+\/(?:edit|approve))\//.test(new URL(model.url).pathname);
        setModel((previous: any) => previous ? { ...previous, width: wide ? 76 : previous.compact ? 42 : 56 } : previous);
        sizing.current = new ResizeObserver(measure);
        sizing.current.observe(content);
        if (heading) sizing.current.observe(heading);
        measure();
      }
      const url = new URL(frame.current!.contentWindow!.location.href);
      const hasError = !!doc.querySelector('[data-message-level~="error"],.errorlist,.field-has-error,[role="alert"][data-error]');
      const success = !!doc.querySelector('[data-message-level~="success"]');
      // Never treat an expired session or permission page as a successful save.
      const isWorkspace = !!doc.querySelector('[data-ui-workspace]');
      if (submitted.current && !stayOpen.current && isWorkspace && !hasError && (success || url.pathname !== new URL(model.url).pathname)) { void close(true); return; }
      if (success && submitted.current) { changed.current = true; dirty.current = false; }
      submitted.current = false;
      if (hasError) requestAnimationFrame(() => {
        const field = doc.querySelector<HTMLElement>('.field-has-error .ui-combo-trigger,.field-has-error input:not([type=hidden]),.field-has-error textarea,.errorlist');
        field?.focus(); field?.scrollIntoView({block:'center'});
      });
      const title = doc.querySelector('h1,h2')?.textContent?.trim();
      if (title) setModel((previous: any) => previous ? { ...previous, title } : previous);
    } catch (_) { /* Keep the dialog available so the user can close a redirected session. */ }
  };
  return <Dialog open={!!model} onOpenChange={value => { if (!value) void close(); }}>
    <DialogContent style={{ "--ui-form-width": `${model?.width || 56}rem` } as React.CSSProperties} className="ui-form-dialog" onOpenAutoFocus={event => { event.preventDefault(); requestAnimationFrame(() => frame.current?.focus()); }} onCloseAutoFocus={event => event.preventDefault()}>
      <div className="ui-dialog-heading"><DialogTitle>{model?.title || 'Edit record'}</DialogTitle><DialogDescription className="sr-only">Complete the form. Validation and saving follow the same business rules as the full page.</DialogDescription>
      {loaded && shareSource && <SnapshotMenu source={shareSource}/>}
      </div>
      {!loaded && <p role="status" className="ui-form-loading">Loading form…</p>}
      {model && <iframe ref={frame} src={model.url} name="inprofic-form-dialog" title={model.title || 'Record form'} onLoad={ready} className="ui-form-frame"/>}
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
      trigger.current = document.activeElement as HTMLElement;
      node.dataset.uiDialogWidth = getComputedStyle(node).maxWidth === 'none' ? '68rem' : getComputedStyle(node).maxWidth;
      placeholder.current = document.createComment('dialog location'); node.before(placeholder.current);
      node.setAttribute('open',''); setDialog(node);
    };
  }, []);
  useEffect(() => {
    closeLegacy = (node, value) => { if (node === dialog) finish(value); };
    if (dialog && content.current) content.current.append(dialog);
  }, [dialog]);
  return <Dialog open={!!dialog} onOpenChange={value => { if (!value) { const event = new Event('cancel', {cancelable:true}); if (dialog?.dispatchEvent(event)) finish(); } }}>
    <DialogContent style={{"--ui-legacy-width":dialog?.dataset.uiDialogWidth || "68rem"} as React.CSSProperties} className="ui-legacy-dialog" onCloseAutoFocus={event => event.preventDefault()}>
      <DialogTitle className="sr-only">{dialog?.querySelector('h1,h2,h3')?.textContent || dialog?.getAttribute('aria-label') || 'Details'}</DialogTitle>
      <DialogDescription className="sr-only">Review the details and available actions.</DialogDescription>
      <div ref={node => { content.current = node; if (node && dialog) { node.append(dialog); dialog.removeAttribute("aria-hidden"); } }} data-ui-legacy/>
    </DialogContent>
  </Dialog>;
}

function SelectCombo({ select }: { select: HTMLSelectElement }) {
  const [open, setOpen] = useState(false), [revision, refresh] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const options = Array.from(select.options).filter(option => !option.hidden && !option.closest('optgroup')?.disabled);
  const selected = options.filter(option => option.selected);
  const label = select.getAttribute('aria-label') || document.querySelector(`label[for="${CSS.escape(select.id)}"]`)?.textContent?.trim() || select.name.replaceAll('_',' ') || 'Choose an option';
  useEffect(() => {
    const update = () => refresh(value => value + 1);
    const invalid = (event: Event) => { event.preventDefault(); trigger.current?.focus(); trigger.current?.setAttribute('aria-invalid','true'); };
    const labelClick = (event: Event) => { if ((event.target as Element).closest('button,a,input')) return; event.preventDefault(); trigger.current?.focus(); };
    const labels = Array.from(select.labels || []); labels.forEach(label => label.addEventListener('click',labelClick));
    const observer = new MutationObserver(update); observer.observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','selected','label','required']});
    select.addEventListener('change',update); select.addEventListener('ms-refresh',update); select.addEventListener('invalid',invalid);
    const reset = () => queueMicrotask(update); select.form?.addEventListener('reset',reset);
    // Existing page scripts often assign select.value without dispatching an
    // event. Refresh on focus/open as well as option mutations.
    const focus = () => update(); window.addEventListener('focus',focus);
    return () => { labels.forEach(label => label.removeEventListener('click',labelClick)); observer.disconnect(); select.removeEventListener('change',update); select.removeEventListener('ms-refresh',update); select.removeEventListener('invalid',invalid); select.form?.removeEventListener('reset',reset); window.removeEventListener('focus',focus); };
  }, [select]);
  const choose = (option: HTMLOptionElement) => {
    if (option.disabled || select.disabled) return;
    if (select.multiple) option.selected = !option.selected; else select.value = option.value;
    notifyNative(select); refresh(value => value + 1); if (!select.multiple) setOpen(false);
  };
  return <Popover open={open} onOpenChange={value => { refresh(n => n + 1); setOpen(value); }}>
    <PopoverTrigger asChild><button ref={trigger} type="button" className="ui-combo-trigger" role="combobox" aria-expanded={open} aria-label={label} aria-required={select.required} aria-invalid={select.getAttribute('aria-invalid') || undefined} disabled={select.disabled} onFocus={() => refresh(n => n + 1)}>
      <span>{select.multiple ? (selected.length ? selected.map(option => option.label).join(', ') : select.dataset.placeholder || 'Choose options') : selected[0]?.label || 'Choose an option'}</span><ChevronsUpDown aria-hidden="true" size={15}/>
    </button></PopoverTrigger>
    <PopoverContent aria-label={`${label} options`} className="ui-combo-popover" align="start">
      <Command key={revision} loop><CommandInput placeholder="Search options…" aria-label={`Search ${label}`}/><CommandList><CommandEmpty>No matching options.</CommandEmpty>
        {options.map((option,index) => <CommandItem key={index} value={`${index} ${option.label}`} disabled={option.disabled} onSelect={() => choose(option)} aria-selected={option.selected}><Check aria-hidden="true" size={14} style={{visibility:option.selected?'visible':'hidden'}}/><span>{option.label || 'None'}</span></CommandItem>)}
      </CommandList></Command>
    </PopoverContent>
  </Popover>;
}

function PublicNavigation() {
  return <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="workspace-icon-button" aria-label="Explore INPROFIC">Explore</button></DropdownMenuTrigger><DropdownMenuContent className="ui-menu" align="end">{[['#about','About INPROFIC'],['#interactive-demo','Interactive walkthrough'],['#capabilities','Connected modules'],['#workflows','Stock to sale'],['#plans','Plans and features'],['#faq','Questions and answers']].map(([href,label])=><DropdownMenuItem key={href} asChild><a href={href}>{label}</a></DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu>;
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
  return <Popover><PopoverTrigger asChild><button type="button" className="workspace-icon-button" aria-label="Appearance"><span aria-hidden="true" dangerouslySetInnerHTML={{__html: button.innerHTML}}/></button></PopoverTrigger>
    <PopoverContent className="ui-appearance-content" align="end"><div ref={attach}/></PopoverContent>
  </Popover>;
}

const selectRoots = new Map<HTMLSelectElement, {root: ReturnType<typeof createRoot>, mount: HTMLElement}>();
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
    select.classList.add('ui-native-select'); select.setAttribute('aria-hidden','true'); select.tabIndex = -1;
    const mount = document.createElement('span'); mount.dataset.uiComboRoot = ''; select.after(mount);
    const reactRoot = createRoot(mount); selectRoots.set(select,{root:reactRoot,mount}); reactRoot.render(<SelectCombo select={select}/>);
  });
  selectRoots.forEach((value,select) => { if (!select.isConnected) { value.root.unmount(); value.mount.remove(); selectRoots.delete(select); } });
}

function boot() {
  const mount = document.createElement('div'); mount.dataset.uiRoot=''; document.body.append(mount);
  createRoot(mount).render(<><ConfirmHost/>{!embedded && <><FormHost/><LegacyHost/></>}</>);
  document.querySelectorAll<HTMLElement>('[data-ui-snapshot-menu]').forEach(node => {
    const source = node.closest<HTMLElement>('[data-snapshot-share]');
    if (source) createRoot(node).render(<SnapshotMenu source={source}/>);
  });
  document.querySelectorAll('[data-ui-site-menu]').forEach(node => createRoot(node).render(<PublicNavigation/>));
  document.querySelectorAll<HTMLElement>('[data-appearance-root]').forEach(node => {
    const button = node.querySelector<HTMLElement>('[data-appearance-toggle]'), panel = node.querySelector<HTMLElement>('[data-appearance-panel]');
    if (!button || !panel) return;
    const slot = document.createElement('span'); node.append(slot); button.style.display='none';
    createRoot(slot).render(<AppearancePopover panel={panel} button={button}/>);
  });
  enhanceSelections();
  let queued = false;
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; enhanceSelections(); bridgeDialogs(); }); } }).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','style']});
  const approved = new WeakSet<HTMLFormElement>();
  document.addEventListener('submit', async event => {
    const form = event.target as HTMLFormElement;
    if (approved.delete(form)) return;
    const code = form.getAttribute('onsubmit') || '';
    const match = code.match(/^\s*return\s+confirm\((['"])([\s\S]*)\1\);?\s*$/);
    if (!match || !confirmRequest) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const message = match[2].replace(/\\(['"\\nrt])/g,(_,c) => ({n:'\n',r:'\r',t:'\t'}[c] || c));
    if (await confirmRequest(message)) { form.onsubmit = null; form.removeAttribute('onsubmit'); approved.add(form); form.requestSubmit((event as SubmitEvent).submitter as HTMLElement | null); }
  },true);
  document.addEventListener('click', async event => {
    const mouse = event as MouseEvent, link = (event.target as Element).closest<HTMLAnchorElement>('a[href]');
    if (!link || event.defaultPrevented || mouse.button || mouse.metaKey || mouse.ctrlKey || mouse.shiftKey || mouse.altKey || link.target || link.hasAttribute('download') || link.hasAttribute('data-no-dialog')) return;
    const url = new URL(link.href,location.href);
    if (embedded) {
      if (/^(cancel|back|return)\b/i.test(link.textContent?.trim() || '')) { event.preventDefault(); postParent('cancel'); }
      return;
    }
    if (url.origin !== location.origin || !FORM_PATH.test(url.pathname) || !openForm) return;
    event.preventDefault();
    if (link.dataset.confirm && !(await confirmRequest(link.dataset.confirm, false, 'edit-payment'))) return;
    openForm(url.href,link.getAttribute('aria-label') || link.textContent?.trim().replace(/^\+\s*/,'') || 'Record form',link);
  });
  if (embedded) {
    document.documentElement.dataset.uiEmbedded='true';
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
    new MutationObserver(syncAppearance).observe(window.parent.document.documentElement, {attributes:true});
    document.addEventListener('keydown',event => {
      if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector('dialog[open],[role=dialog][data-state=open],[role=alertdialog]')) { event.preventDefault(); postParent('cancel'); }
    });
    document.addEventListener('input',()=>postParent('dirty')); document.addEventListener('change',()=>postParent('dirty'));
    document.addEventListener('submit',event => { queueMicrotask(() => { if (!event.defaultPrevented) postParent('submit', {stayOpen: ((event as SubmitEvent).submitter?.getAttribute('name') === 'save_add_new' || (event as SubmitEvent).submitter?.hasAttribute('data-stay-open'))}); }); });
  }
  bridgeDialogs(); document.documentElement.dataset.uiReady='true';
}
function bridgeDialogs() {
  if (embedded) return;
  document.querySelectorAll<HTMLDialogElement>('dialog:not([data-native-dialog])').forEach(dialog => {
    if (dialog.dataset.uiDialog) return; dialog.dataset.uiDialog='true';
    Object.defineProperty(dialog,'showModal',{configurable:true,value:() => openLegacy?.(dialog)});
    Object.defineProperty(dialog,'close',{configurable:true,value:(value='') => closeLegacy?.(dialog,value)});
  });
}
if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot); else boot();
