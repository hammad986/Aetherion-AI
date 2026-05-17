(function () {
        var _fbCat = 'bug';

        window.openFeedbackModal = function () {
          document.getElementById('fb-form-body').style.display = '';
          document.getElementById('fb-success-body').style.display = 'none';
          document.getElementById('fb-err').style.display = 'none';
          document.getElementById('fb-err').textContent = '';
          document.getElementById('feedback-overlay').classList.add('open');
          setTimeout(function () { document.getElementById('fb-message').focus(); }, 80);
        };

        window.closeFeedbackModal = function () {
          document.getElementById('feedback-overlay').classList.remove('open');
        };

        window.closeFeedbackOnOverlay = function (e) {
          if (e.target === document.getElementById('feedback-overlay')) closeFeedbackModal();
        };

        window.selectFbCat = function (el) {
          document.querySelectorAll('.fb-cat').forEach(function (c) { c.classList.remove('selected'); });
          el.classList.add('selected');
          _fbCat = el.getAttribute('data-cat');
        };

        window.submitFeedback = function () {
          var msg = (document.getElementById('fb-message').value || '').trim();
          var email = (document.getElementById('fb-email').value || '').trim();
          var errEl = document.getElementById('fb-err');
          var btn = document.getElementById('fb-submit-btn');

          if (!msg || msg.length < 5) {
            errEl.textContent = 'Please enter at least 5 characters.';
            errEl.style.display = 'block';
            return;
          }
          errEl.style.display = 'none';
          btn.disabled = true;
          btn.textContent = 'Sending…';

          var token = '';
          try { token = localStorage.getItem('nx_access_token') || ''; } catch (e) { }

          fetch('/api/feedback', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': token ? ('Bearer ' + token) : '',
            },
            body: JSON.stringify({ category: _fbCat, message: msg, email: email }),
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              btn.disabled = false;
              btn.textContent = 'Send Report';
              if (data.ok) {
                document.getElementById('fb-form-body').style.display = 'none';
                document.getElementById('fb-success-body').style.display = '';
                document.getElementById('fb-message').value = '';
                document.getElementById('fb-email').value = '';
                setTimeout(closeFeedbackModal, 2800);
              } else {
                errEl.textContent = data.error || 'Submission failed. Please try again.';
                errEl.style.display = 'block';
              }
            })
            .catch(function () {
              btn.disabled = false;
              btn.textContent = 'Send Report';
              errEl.textContent = 'Network error. Please try again.';
              errEl.style.display = 'block';
            });
        };

        document.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') closeFeedbackModal();
        });

        console.debug('[Feedback] ready');
      })();
