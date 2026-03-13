/**
 * Entrance animations: reveal on scroll / in view
 * Respects prefers-reduced-motion
 */
(function () {
  if (typeof window.matchMedia === 'undefined') return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  function isInView(el) {
    var rect = el.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  }

  function run() {
    var reveals = document.querySelectorAll('.reveal, .reveal-children');
    if (!reveals.length) return;

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('in-view');
          }
        });
      },
      { rootMargin: '0px 0px -40px 0px', threshold: 0.05 }
    );

    reveals.forEach(function (el) {
      if (isInView(el)) {
        el.classList.add('in-view');
      }
      observer.observe(el);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
