/* User-initiated sharing only. Record text is supplied by the authorised view. */
(() => {
  async function snapshots(root) {
    if (!root.dataset.imageUrl) throw new Error('Save this record before sharing a snapshot.');
    const response = await fetch(root.dataset.imageUrl, {credentials:'same-origin'});
    if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/png')) throw new Error('Could not load the snapshot. Refresh the page and try again.');
    return [new File([await response.blob()], (root.dataset.filename || 'summary') + '.png', {type:'image/png'})];
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-snapshot-action]'); if (!button) return;
    const root = button.closest('[data-snapshot-share]');
    const status = root.querySelector('[data-snapshot-status]');
    const text = JSON.parse(document.getElementById(root.dataset.textId).textContent);
    const action = button.dataset.snapshotAction;
    const textUrl = 'https://wa.me/?text=' + encodeURIComponent(text);
    if (action === 'text') { window.open(textUrl, '_blank', 'noopener'); return; }
    button.disabled = true;
    let preview = null;
    // Reserve a window in the user gesture, before asynchronous image creation.
    if (action === 'view') { preview = window.open('about:blank', '_blank'); if (preview) preview.opener = null; }
    try {
      if (action === 'copy') { await navigator.clipboard.writeText(text); status.textContent = 'Text copied.'; return; }
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
          await navigator.clipboard.writeText(text);
          status.textContent = 'Image saved and full text copied. Attach the image and paste the report in WhatsApp.';
        } catch (_) {
          status.textContent = 'Image saved. Use Copy text for the full report, then attach the image in WhatsApp.';
        }
      } else status.textContent = action === 'view' ? 'Snapshot opened.' : 'Image saved.';
    } catch (error) {
      if (error.name === 'AbortError') status.textContent = '';
      else { preview?.close(); status.textContent = error.message || 'Unable to prepare the snapshot. Use WhatsApp text instead.'; }
    } finally { button.disabled = false; }
  });
})();
