/* ==========================================================================
   admin-editor.js — the post editor

   Responsibilities, in order below:
     1. TinyMCE (self-hosted, no CDN/API key) with the menubar + toolbar
     2. Tabs (Post Settings / SEO Settings)
     3. Title -> slug -> permalink/SERP mirroring
     4. Character counters and the SEO checklist
     5. Image uploads: featured image dropzone + author avatar
     6. Tag chips, Import menu
     7. Dirty tracking ("Unsaved changes" + a beforeunload guard)

   Vanilla JS, no framework, matching static/js/admin.js.
   ========================================================================== */
(function () {
  'use strict';

  var CFG = window.EDITOR_CONFIG || {};
  var form = document.getElementById('post-form');
  if (!form) return;

  var $ = function (id) { return document.getElementById(id); };

  var dirty = false;         // has the user changed anything since load/save?
  var submitting = false;    // suppresses the beforeunload guard on submit
  var booted = false;        // true once TinyMCE has loaded the initial body

  function markDirty() {
    if (dirty || submitting) return;
    dirty = true;
    var el = $('ed-dirty');
    if (el) el.hidden = false;
  }

  function resetDirty() {
    dirty = false;
    var el = $('ed-dirty');
    if (el) el.hidden = true;
  }

  // --- 1. Rich text editor ------------------------------------------------
  var bodyArea = $('post-body');

  if (window.tinymce && bodyArea) {
    tinymce.init({
      target: bodyArea,
      base_url: CFG.tinymceBase,
      license_key: 'gpl',              // self-hosted community build
      height: 520,
      menubar: 'edit view insert format table',
      plugins: 'advlist autolink lists link image media table code',
      toolbar:
        'undo redo | blocks | bold italic underline strikethrough | ' +
        'forecolor backcolor | alignleft aligncenter alignright alignjustify | ' +
        'bullist numlist outdent indent | blockquote link image media table | ' +
        'removeformat code',
      toolbar_mode: 'wrap',
      branding: false,
      promotion: false,
      statusbar: false,
      content_style:
        'body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;' +
        'font-size:16px;line-height:1.6;color:#14181f}',
      // Images dropped or pasted into the body go through the same endpoint as
      // the featured image, so everything lands in static/uploads/.
      images_upload_handler: function (blobInfo) {
        return uploadFile(blobInfo.blob(), blobInfo.filename())
          .then(function (res) { return res.location; });
      },
      automatic_uploads: true,
      setup: function (ed) {
        ed.on('change keyup SetContent', function () {
          ed.save();                   // mirror into the textarea for submit
          // SetContent also fires while TinyMCE loads the existing body, which
          // would show "Unsaved changes" before the user has touched anything.
          if (booted) markDirty();
          updateReadTime();
        });
      },
      init_instance_callback: function () {
        booted = true;
        updateReadTime();
        resetDirty();
      }
    });
  }

  function bodyText() {
    if (window.tinymce && tinymce.activeEditor && !tinymce.activeEditor.isHidden()) {
      return tinymce.activeEditor.getContent({ format: 'text' });
    }
    return bodyArea ? bodyArea.value.replace(/<[^>]+>/g, ' ') : '';
  }

  // Keep the read-time field in step with what's actually typed (200 wpm, the
  // same rate db.read_time_minutes() uses server-side).
  function updateReadTime() {
    var field = $('post-readtime');
    if (!field) return;
    var words = bodyText().trim().split(/\s+/).filter(Boolean).length;
    field.value = words ? Math.max(1, Math.round(words / 200)) + ' min' : '—';
  }

  // --- 2. Tabs ------------------------------------------------------------
  var tabs = [
    { btn: $('tab-post'), panel: $('panel-post') },
    { btn: $('tab-seo'), panel: $('panel-seo') }
  ];
  tabs.forEach(function (t, i) {
    if (!t.btn) return;
    t.btn.addEventListener('click', function () {
      tabs.forEach(function (other, j) {
        var on = i === j;
        other.btn.classList.toggle('is-active', on);
        other.btn.setAttribute('aria-selected', on ? 'true' : 'false');
        other.panel.hidden = !on;
      });
    });
  });

  // --- 3. Title -> slug -> permalink / SERP -------------------------------
  var titleEl = $('post-title');
  var slugEl = $('post-slug');
  var seoTitleEl = $('post-seotitle');
  var metaEl = $('post-meta');

  function slugify(value) {
    return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }

  // A slug that's already been hand-edited (or loaded from a saved post) is
  // never overwritten — changing it would break links to a published post.
  var slugPristine = !slugEl || slugEl.value.trim() === '';
  if (slugEl) slugEl.addEventListener('input', function () { slugPristine = false; });

  function syncFromTitle() {
    var title = titleEl ? titleEl.value : '';
    if (slugEl && slugPristine) slugEl.value = slugify(title);
    setText('permalink-slug', slugEl ? slugEl.value : '');
    setText('serp-slug', slugEl ? slugEl.value : '');
    setText('serp-title', (seoTitleEl && seoTitleEl.value) || title || 'Post title');
    if (seoTitleEl) seoTitleEl.placeholder = title || 'Add a Post Title to populate this';
    count('title-counter', title.length);
    count('seo-counter', ((seoTitleEl && seoTitleEl.value) || title).length);
    updateChecks();
  }

  function setText(id, value) { var el = $(id); if (el) el.textContent = value; }

  function count(id, len) {
    var el = $(id);
    if (!el) return;
    var max = parseInt(el.dataset.max, 10) || 60;
    el.textContent = len + '/' + max;
    el.classList.toggle('is-over', len > max);
    el.classList.toggle('is-good', len > 0 && len <= max);
  }

  function updateChecks() {
    var titleLen = ((seoTitleEl && seoTitleEl.value) ||
                    (titleEl && titleEl.value) || '').length;
    var descLen = (metaEl && metaEl.value || '').length;
    setCheck('title', titleLen, 60, 15);
    setCheck('desc', descLen, 160, 70);
  }

  function setCheck(name, len, max, min) {
    var li = document.querySelector('#seo-checks li[data-check="' + name + '"]');
    if (!li) return;
    var ok = len >= min && len <= max;
    li.classList.toggle('is-ok', ok);
    li.querySelector('.ed-checkmark').textContent = ok ? '✓' : '!';
    li.querySelector('span:last-child').textContent = len + '/' + max;
  }

  if (titleEl) titleEl.addEventListener('input', function () { syncFromTitle(); markDirty(); });
  if (slugEl) slugEl.addEventListener('input', function () { syncFromTitle(); markDirty(); });
  if (seoTitleEl) seoTitleEl.addEventListener('input', function () { syncFromTitle(); markDirty(); });
  if (metaEl) {
    metaEl.addEventListener('input', function () {
      count('meta-counter', metaEl.value.length);
      setText('serp-desc', metaEl.value || 'Your meta description appears here.');
      updateChecks();
      markDirty();
    });
  }

  // --- 4. Status select <-> hidden field + chip ---------------------------
  var statusSel = $('post-status');
  var statusVal = $('post-status-value');
  if (statusSel && statusVal) {
    statusSel.addEventListener('change', function () {
      var v = statusSel.value;
      statusVal.value = v;
      var dot = $('status-dot');
      if (dot) dot.className = 'ed-dot ed-dot--' + v;
      var chip = $('ed-statuschip');
      if (chip) {
        chip.className = 'ed-statuschip ed-statuschip--' + v;
        chip.innerHTML = '<span class="ed-dot" aria-hidden="true"></span>' +
          v.charAt(0).toUpperCase() + v.slice(1);
      }
      markDirty();
    });
  }

  // --- Publish date: keep a real placeholder AND the native picker --------
  // A datetime-local input can't show a placeholder, so the field renders as
  // text until it's focused, then becomes a real date-time control.
  var pubEl = $('post-published');
  if (pubEl) {
    pubEl.addEventListener('focus', function () {
      if (pubEl.type === 'text') {
        pubEl.type = 'datetime-local';
        // Chromium needs a nudge to open the picker on the same interaction.
        if (pubEl.showPicker) { try { pubEl.showPicker(); } catch (e) { /* ignore */ } }
      }
    });
    pubEl.addEventListener('blur', function () {
      if (!pubEl.value) pubEl.type = 'text';
    });
  }

  // --- 5. Uploads ---------------------------------------------------------
  var fileInput = $('upload-input');
  var pendingTarget = null;   // 'cover' | 'avatar'

  function uploadFile(file, name) {
    var data = new FormData();
    data.append('file', file, name || file.name);
    return fetch(CFG.uploadUrl, {
      method: 'POST', body: data, credentials: 'same-origin'
    }).then(function (res) {
      return res.json().catch(function () {
        // A 413 from Flask is an HTML error page, not JSON.
        throw new Error(res.status === 413
          ? 'That image is too large.'
          : 'Upload failed (' + res.status + ').');
      }).then(function (json) {
        if (!res.ok) throw new Error(json.error || 'Upload failed.');
        return json;
      });
    });
  }

  function showCover(url) {
    var drop = $('cover-drop'), img = $('cover-preview'),
        prompt = drop && drop.querySelector('.ed-drop__prompt'),
        clear = $('cover-clear'), hidden = $('post-cover'), og = $('post-og');
    if (hidden) hidden.value = url || '';
    if (og) og.value = url || '';
    if (img) { img.src = url || ''; img.hidden = !url; }
    if (prompt) prompt.hidden = !!url;
    if (clear) clear.hidden = !url;
    if (drop) drop.classList.toggle('has-image', !!url);
    markDirty();
  }

  function showAvatar(url) {
    var img = $('avatar-preview'), initials = $('avatar-initials'),
        clear = $('avatar-clear'), hidden = $('post-avatar');
    if (hidden) hidden.value = url || '';
    if (img) { img.src = url || ''; img.hidden = !url; }
    if (initials) initials.hidden = !!url;
    if (clear) clear.hidden = !url;
    markDirty();
  }

  function coverError(message) {
    var el = $('cover-error');
    if (!el) return;
    el.textContent = message || '';
    el.hidden = !message;
  }

  function handleUpload(file, target) {
    var drop = $('cover-drop');
    coverError('');
    if (target === 'cover' && drop) drop.classList.add('is-busy');
    uploadFile(file)
      .then(function (json) {
        if (target === 'avatar') showAvatar(json.url);
        else showCover(json.url);
      })
      .catch(function (err) {
        if (target === 'cover') coverError(err.message);
        else window.alert(err.message);
      })
      .then(function () { if (drop) drop.classList.remove('is-busy'); });
  }

  if (fileInput) {
    fileInput.addEventListener('change', function () {
      if (fileInput.files && fileInput.files[0]) {
        handleUpload(fileInput.files[0], pendingTarget);
      }
      fileInput.value = '';   // so picking the same file twice still fires
    });
  }

  function pick(target) {
    pendingTarget = target;
    if (fileInput) fileInput.click();
  }

  var coverDrop = $('cover-drop');
  if (coverDrop) {
    coverDrop.addEventListener('click', function (e) {
      if (e.target.closest('#cover-clear')) return;
      pick('cover');
    });
    coverDrop.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick('cover'); }
    });
    ['dragenter', 'dragover'].forEach(function (evt) {
      coverDrop.addEventListener(evt, function (e) {
        e.preventDefault();
        coverDrop.classList.add('is-dragging');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      coverDrop.addEventListener(evt, function () {
        coverDrop.classList.remove('is-dragging');
      });
    });
    coverDrop.addEventListener('drop', function (e) {
      e.preventDefault();
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) handleUpload(f, 'cover');
    });
  }

  var coverClear = $('cover-clear');
  if (coverClear) {
    coverClear.addEventListener('click', function (e) {
      e.stopPropagation();
      showCover('');
    });
  }
  var avatarPick = $('avatar-pick');
  if (avatarPick) avatarPick.addEventListener('click', function () { pick('avatar'); });
  var avatarClear = $('avatar-clear');
  if (avatarClear) avatarClear.addEventListener('click', function () { showAvatar(''); });

  // Author initials follow the name field while there's no avatar image.
  var authorEl = $('post-author');
  if (authorEl) {
    authorEl.addEventListener('input', function () {
      var initials = $('avatar-initials');
      if (initials) initials.textContent = authorEl.value.slice(0, 2).toUpperCase();
      markDirty();
    });
  }

  // --- 6. Tag chips -------------------------------------------------------
  var tagInput = $('tag-input');
  var tagChips = $('tag-chips');
  var tagHidden = $('post-tags');

  function currentTags() {
    return Array.prototype.map.call(
      tagChips ? tagChips.querySelectorAll('.ed-chip') : [],
      function (li) { return li.dataset.tag; });
  }

  function syncTags() {
    if (tagHidden) tagHidden.value = currentTags().join(', ');
    markDirty();
  }

  function addTag(raw) {
    var tag = (raw || '').trim().replace(/,+$/, '');
    if (!tag || !tagChips) return;
    var exists = currentTags().some(function (t) {
      return t.toLowerCase() === tag.toLowerCase();
    });
    if (exists) return;

    var li = document.createElement('li');
    li.className = 'ed-chip';
    li.dataset.tag = tag;
    li.textContent = tag;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Remove tag ' + tag);
    btn.innerHTML = '&times;';
    li.appendChild(btn);
    tagChips.appendChild(li);
    syncTags();
  }

  if (tagInput) {
    tagInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        addTag(tagInput.value);
        tagInput.value = '';
      } else if (e.key === 'Backspace' && !tagInput.value) {
        var last = tagChips && tagChips.lastElementChild;
        if (last) { last.remove(); syncTags(); }
      }
    });
    // A pasted "a, b, c" becomes three chips.
    tagInput.addEventListener('paste', function (e) {
      var text = (e.clipboardData || window.clipboardData).getData('text');
      if (text && text.indexOf(',') > -1) {
        e.preventDefault();
        text.split(',').forEach(addTag);
      }
    });
  }
  if (tagChips) {
    tagChips.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (btn) { btn.parentElement.remove(); syncTags(); }
    });
  }

  // --- Import menu --------------------------------------------------------
  var importToggle = $('import-toggle');
  var importMenu = $('import-menu');
  if (importToggle && importMenu) {
    importToggle.addEventListener('click', function () {
      var open = importMenu.hidden;
      importMenu.hidden = !open;
      importToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!importMenu.hidden && !e.target.closest('.ed-import')) {
        importMenu.hidden = true;
        importToggle.setAttribute('aria-expanded', 'false');
      }
    });
    importMenu.addEventListener('click', function (e) {
      var item = e.target.closest('[data-import]');
      if (!item) return;
      var kind = item.dataset.import;
      var text = window.prompt(kind === 'html'
        ? 'Paste HTML to append to the post:'
        : 'Paste text to append to the post (blank lines become paragraphs):');
      if (!text) return;
      insertContent(kind === 'html' ? text : textToHtml(text));
      importMenu.hidden = true;
    });
  }

  var importFile = $('import-file');
  if (importFile) {
    importFile.addEventListener('change', function () {
      var f = importFile.files && importFile.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        var raw = String(reader.result);
        insertContent(/\.(html?|htm)$/i.test(f.name) ? raw : textToHtml(raw));
        if (importMenu) importMenu.hidden = true;
      };
      reader.readAsText(f);
      importFile.value = '';
    });
  }

  function textToHtml(text) {
    return text.replace(/\r\n/g, '\n').split(/\n{2,}/)
      .filter(function (b) { return b.trim(); })
      .map(function (b) { return '<p>' + b.trim().replace(/\n/g, '<br>') + '</p>'; })
      .join('');
  }

  function insertContent(html) {
    if (window.tinymce && tinymce.activeEditor) {
      tinymce.activeEditor.insertContent(html);
      tinymce.activeEditor.save();
    } else if (bodyArea) {
      bodyArea.value += html;
    }
    markDirty();
    updateReadTime();
  }

  // --- 7. Dirty tracking --------------------------------------------------
  // markDirty / resetDirty are declared at the top because tinymce.init above
  // references them.
  form.addEventListener('input', markDirty);
  form.addEventListener('change', markDirty);

  form.addEventListener('submit', function (e) {
    // Preview opens in a new tab, so the page itself isn't navigating away and
    // the work is still unsaved — don't clear the flag for it.
    var isPreview = e.submitter && e.submitter.getAttribute('formtarget') === '_blank';
    if (window.tinymce && tinymce.activeEditor) tinymce.activeEditor.save();
    if (!isPreview) submitting = true;
  });

  window.addEventListener('beforeunload', function (e) {
    if (!dirty || submitting) return;
    e.preventDefault();
    e.returnValue = '';
    return '';
  });

  // --- Initial paint ------------------------------------------------------
  syncFromTitle();
  if (metaEl) {
    count('meta-counter', metaEl.value.length);
    setText('serp-desc', metaEl.value || 'Your meta description appears here.');
  }
  updateChecks();
  updateReadTime();
  syncTags();
  // The calls above run the same handlers a user edit would, so clear the flag
  // they set. TinyMCE clears it again from init_instance_callback once it has
  // finished loading the existing body.
  resetDirty();
})();
