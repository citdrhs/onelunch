function togglePassword(fieldId, btn) {
  var el = document.getElementById(fieldId);
  if (!el) return;
  if (el.type === 'password') {
    el.type = 'text';
    btn.textContent = '🙈';
  } else {
    el.type = 'password';
    btn.textContent = '👁️';
  }
}

(function () {
  // csrf on fetch
  var _fetch = window.fetch;
  window.fetch = function (url, opts) {
    opts = opts || {};
    var t = document.querySelector('meta[name="csrf-token"]');
    if (t && t.content) {
      opts.headers = Object.assign({}, opts.headers || {});
      if (!opts.headers['X-CSRFToken'] && !opts.headers['x-csrf-token']) {
        opts.headers['X-CSRFToken'] = t.content;
      }
    }
    return _fetch(url, opts);
  };
})();
