/* User-initiated sharing only. Record text is supplied by the authorised view. */
(() => {
  async function snapshots(root) {
    if (!root.dataset.imageUrl) throw new Error('Save this record before sharing a snapshot.');
    const response = await fetch(root.dataset.imageUrl, {credentials:'same-origin'});
    if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/png')) throw new Error('Could not load the snapshot. Refresh the page and try again.');
    return [new File([await response.blob()], (root.dataset.filename || 'summary') + '.png', {type:'image/png'})];
  }
  async function copyText(text) {
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
    } catch (_) { /* Use the focused document's legacy copy when the API is unavailable. */ }
    const active = document.activeElement;
    const input = document.createElement('textarea');
    input.value = text; input.setAttribute('aria-label', 'Summary text');
    input.style.cssText = 'position:fixed;left:-9999px;top:0';
    (document.querySelector('[role="dialog"][data-state="open"]') || document.body).append(input);
    try {
      input.focus({preventScroll:true}); input.select();
      if (!document.execCommand('copy')) throw new Error('Could not copy text. Try WhatsApp text instead.');
    } finally { input.remove(); active?.focus?.({preventScroll:true}); }
  }
  async function runAction(button) {
    const root = button.closest('[data-snapshot-share]');
    const status = root.querySelector('[data-snapshot-status]');
    const text = JSON.parse(root.ownerDocument.getElementById(root.dataset.textId).textContent);
    const action = button.dataset.snapshotAction;
    const textUrl = 'https://wa.me/?text=' + encodeURIComponent(text);
    if (action === 'text') { window.open(textUrl, '_blank', 'noopener'); return; }
    button.disabled = true;
    let preview = null;
    // Reserve a window in the user gesture, before asynchronous image creation.
    if (action === 'view') { preview = window.open('about:blank', '_blank'); if (preview) preview.opener = null; }
    try {
      if (action === 'copy') { await copyText(text); status.textContent = 'Text copied.'; return; }
      status.textContent = 'Preparing snapshot…';
      const files = await snapshots(root);
      if (action === 'share' && navigator.canShare?.({files})) {
        await navigator.share({files, text}); status.textContent = 'Snapshot ready to share.'; return;
      }
      if (action === 'view' && !preview) throw new Error('Allow pop-ups to view the snapshot, or save the image.');
      for (const file of files) {
        const url = URL.createObjectURL(file);
        if (action === 'view') {
          const img = preview.document.createElement('img'); img.src = url; img.alt = 'Summary snapshot'; img.style.maxWidth = '100%'; preview.document.body.append(img);
        } else { const a = document.createElement('a'); a.href = url; a.download = file.name; a.click(); }
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
      if (action === 'share') {
        try {
          await copyText(text);
          status.textContent = 'Image saved and full text copied. Attach the image and paste the report in WhatsApp.';
        } catch (_) {
          status.textContent = 'Image saved. Use Copy text for the full report, then attach the image in WhatsApp.';
        }
      } else status.textContent = action === 'view' ? 'Snapshot opened.' : 'Image saved.';
    } catch (error) {
      if (error.name === 'AbortError') status.textContent = '';
      else { preview?.close(); status.textContent = error.message || 'Unable to prepare the snapshot. Use WhatsApp text instead.'; }
    } finally { button.disabled = false; }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-snapshot-action]');
    if (button) void runAction(button);
  });
  // Outer modal/menu gestures must use the focused document, even when the
  // authorised snapshot data and source controls live inside an iframe.
  document.addEventListener('inprofic:snapshot-action', event => {
    if (event.detail?.command?.matches('[data-snapshot-action]')) void runAction(event.detail.command);
  });
})();
