'use strict';
(() => {
  const vscode = acquireVsCodeApi();
  const post = (message) => vscode.postMessage(message);
  const el = (tag, props = {}, children = []) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };
  const button = (label, title, onclick) => el('button', { textContent: label, title, onclick, type: 'button' });

  const profile = el('select', { title: 'Chrome profile', onchange: () => profile.value && post({ type: 'attach', profile: profile.value }) });
  const address = el('input', { type: 'text', placeholder: 'Search or enter address', spellcheck: false });
  const follow = button('Follow', 'Follow the tab the agent is using', () => post({ type: 'follow', value: follow.getAttribute('aria-pressed') !== 'true' }));
  const toolbar = el('div', { className: 'toolbar' }, [
    profile,
    button('←', 'Back', () => post({ type: 'back' })),
    button('→', 'Forward', () => post({ type: 'forward' })),
    button('⟳', 'Reload', () => post({ type: 'reload' })),
    address,
    follow,
    button('+', 'New tab', () => post({ type: 'newTab' })),
  ]);
  const tabs = el('div', { className: 'tabs' });
  const frame = el('img', { alt: '', draggable: false });
  const message = el('div', { className: 'message' });
  const start = button('Start', 'Start this profile\'s agent browser', () => profile.value && post({ type: 'attach', profile: profile.value }));
  const overlay = el('div', { className: 'overlay' }, [message, start]);
  const stage = el('div', { className: 'stage', tabIndex: 0 }, [frame, overlay]);
  document.body.append(toolbar, tabs, stage);

  let activeUrl = '';
  const showMessage = (text, withStart = false) => {
    message.textContent = text;
    start.hidden = !withStart;
    overlay.hidden = !text;
  };
  showMessage('Looking for agent browsers…');

  address.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      post({ type: 'navigate', url: address.value });
      stage.focus();
    } else if (event.key === 'Escape') {
      address.value = activeUrl;
      stage.focus();
    }
  });

  const position = (event) => {
    const rect = frame.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  };
  const mods = (e) => ({ alt: e.altKey, ctrl: e.ctrlKey, meta: e.metaKey, shift: e.shiftKey });
  frame.addEventListener('pointerdown', (event) => {
    stage.focus();
    frame.setPointerCapture(event.pointerId);
    post({ type: 'mouse', kind: 'down', ...position(event), button: event.button, buttons: event.buttons, clickCount: event.detail || 1, ...mods(event) });
    event.preventDefault();
  });
  frame.addEventListener('pointerup', (event) => {
    post({ type: 'mouse', kind: 'up', ...position(event), button: event.button, buttons: event.buttons, clickCount: event.detail || 1, ...mods(event) });
  });
  let pendingMove = null;
  frame.addEventListener('pointermove', (event) => {
    const queued = pendingMove;
    pendingMove = { type: 'mouse', kind: 'move', ...position(event), buttons: event.buttons, ...mods(event) };
    if (!queued) requestAnimationFrame(() => (post(pendingMove), (pendingMove = null)));
  });
  frame.addEventListener('contextmenu', (event) => event.preventDefault());
  stage.addEventListener('wheel', (event) => {
    post({ type: 'mouse', kind: 'wheel', ...position(event), deltaX: event.deltaX, deltaY: event.deltaY, ...mods(event) });
    event.preventDefault();
  }, { passive: false });

  const key = (kind) => (event) => {
    const shortcut = event.metaKey || event.ctrlKey;
    if (shortcut && event.key.toLowerCase() === 'v') return; // handled by the paste event
    if (shortcut && event.key.toLowerCase() === 'c' && kind === 'down') {
      post({ type: 'copy' });
      return;
    }
    post({ type: 'key', kind, key: event.key, code: event.code, ...mods(event) });
    event.preventDefault();
  };
  stage.addEventListener('keydown', key('down'));
  stage.addEventListener('keyup', key('up'));
  stage.addEventListener('paste', (event) => {
    post({ type: 'paste', text: event.clipboardData?.getData('text/plain') ?? '' });
    event.preventDefault();
  });

  new ResizeObserver(([entry]) => {
    const ratio = window.devicePixelRatio || 1;
    post({ type: 'resize', width: entry.contentRect.width * ratio, height: entry.contentRect.height * ratio });
  }).observe(stage);

  const label = (p) => `${p.email || p.name || p.directory}${p.running ? ' ●' : ''}`;
  window.addEventListener('message', ({ data }) => {
    switch (data.type) {
      case 'profiles': {
        const current = profile.value || data.profiles.find((p) => p.attached)?.directory;
        profile.replaceChildren(el('option', { value: '', textContent: 'Choose a profile…', disabled: true }), ...data.profiles
          .filter((p) => p.email || p.running || p.cloned)
          .map((p) => el('option', { value: p.directory, textContent: label(p), selected: p.directory === current })));
        if (!current) profile.selectedIndex = 0;
        if (!data.profiles.some((p) => p.attached) && overlay.hidden === false && !data.profiles.some((p) => p.running)) {
          showMessage('No agent browser is running. Pick a profile and start one, or ask the agent to browse.', true);
        }
        break;
      }
      case 'attached':
        profile.value = data.profile;
        showMessage('');
        break;
      case 'closed':
        frame.removeAttribute('src');
        showMessage('This agent browser closed.', true);
        break;
      case 'frame':
        frame.src = `data:image/jpeg;base64,${data.data}`;
        if (!overlay.hidden && start.hidden) showMessage(''); // a transient "Connecting…" status
        break;
      case 'tabs': {
        follow.setAttribute('aria-pressed', String(data.follow));
        tabs.replaceChildren(...data.tabs.map((t) => {
          const chip = el('div', { className: `tab${t.targetId === data.activeId ? ' active' : ''}`, title: t.url }, [
            el('span', { textContent: t.title || t.url || 'New tab', onclick: () => post({ type: 'selectTab', targetId: t.targetId }) }),
            button('×', 'Close tab', () => post({ type: 'closeTab', targetId: t.targetId })),
          ]);
          return chip;
        }));
        const active = data.tabs.find((t) => t.targetId === data.activeId);
        activeUrl = active?.url ?? '';
        if (document.activeElement !== address) address.value = activeUrl;
        if (!data.tabs.length) showMessage('No open tabs.', false);
        break;
      }
      case 'status':
        showMessage(data.text, !!profile.value);
        break;
    }
  });

  setInterval(() => post({ type: 'profiles' }), 10_000);
  post({ type: 'ready' });
})();
