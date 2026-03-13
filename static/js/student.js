(function () {
    var script = document.getElementById('rooms-data');
    var metaScript = document.getElementById('student-meta');
    var rooms = {};
    var currentDay = 'M';
    var currentLunch = 'B';
    var favorites = [];

    try {
        if (script && script.textContent) rooms = JSON.parse(script.textContent);
        if (metaScript && metaScript.textContent) {
            var meta = JSON.parse(metaScript.textContent);
            currentDay = meta.current_day || 'M';
            currentLunch = meta.current_lunch || 'B';
            favorites = meta.favorites || [];
        }
    } catch (e) { console.warn('Parse rooms/meta failed', e); }

    var dayLabels = { M: 'Mon', T: 'Tue', W: 'Wed', R: 'Thu', F: 'Fri' };
    var mapEl = document.getElementById('room-map');
    var listEl = document.getElementById('room-list-ul');
    var filterDay = document.getElementById('filter-day');
    var filterLunch = document.getElementById('filter-lunch');
    var sortBy = document.getElementById('sort-by');
    var searchInput = document.getElementById('search-rooms');
    var filterApply = document.getElementById('filter-apply');
    var filterClear = document.getElementById('filter-clear');
    var detailEl = document.getElementById('room-detail');
    var detailTitle = document.getElementById('detail-title');
    var detailBody = document.getElementById('detail-body');
    var closeBtn = document.getElementById('close-detail');
    var todayBanner = document.getElementById('today-banner');
    var todayText = document.getElementById('today-text');
    var emptyState = document.getElementById('empty-state');
    var emptyStateClear = document.getElementById('empty-state-clear');
    var btnFavorite = document.getElementById('btn-favorite');

    function setTodayBanner() {
        if (!todayText) return;
        todayText.textContent = dayLabels[currentDay] + ', ' + currentLunch + ' lunch — these rooms are open';
    }

    function roomMatchesSearch(data, q) {
        if (!q || !q.trim()) return true;
        var s = (data.room + ' ' + (data.teacher_name || '')).toLowerCase();
        return s.indexOf(q.trim().toLowerCase()) !== -1;
    }

    function roomOpenToday(data) {
        var am = data.availability_map || {};
        var avail = am[currentDay];
        return avail === currentLunch || avail === 'AB';
    }

    function applyFiltersAndSearch() {
        var day = filterDay ? filterDay.value : 'ALL';
        var lunch = filterLunch ? filterLunch.value : 'ANY';
        var q = searchInput ? searchInput.value : '';
        var filtered = {};
        Object.keys(rooms).forEach(function (r) {
            var data = rooms[r];
            if (!roomMatchesSearch(data, q)) return;
            if (day !== 'ALL') {
                if (!data.available_days || data.available_days.indexOf(day) === -1) return;
                if (lunch !== 'ANY') {
                    var am = data.availability_map || {};
                    var avail = am[day];
                    if (avail !== lunch && avail !== 'AB') return;
                }
            } else if (lunch !== 'ANY') {
                var am = data.availability_map || {};
                var hasLunch = false;
                for (var d in am) { 
                    var avail = am[d];
                    if (avail === lunch || avail === 'AB') { 
                        hasLunch = true; 
                        break; 
                    } 
                }
                if (!hasLunch) return;
            }
            filtered[r] = data;
        });
        return filtered;
    }

    function sortRoomNumbers(roomNumbers, sortOption) {
        var list = roomNumbers.slice();
        if (sortOption === 'teacher') {
            list.sort(function (a, b) {
                var na = (rooms[a].teacher_name || '').toLowerCase();
                var nb = (rooms[b].teacher_name || '').toLowerCase();
                return na.localeCompare(nb) || Number(a) - Number(b);
            });
        } else if (sortOption === 'open-today') {
            list.sort(function (a, b) {
                var openA = roomOpenToday(rooms[a]) ? 1 : 0;
                var openB = roomOpenToday(rooms[b]) ? 1 : 0;
                if (openB !== openA) return openB - openA;
                return Number(a) - Number(b);
            });
        } else {
            list.sort(function (a, b) { return Number(a) - Number(b); });
        }
        return list;
    }

    function showDetail(roomNum, data) {
        detailTitle.textContent = 'Room ' + roomNum;
        var am = data.availability_map || {};
        var availStr = ['M','T','W','R','F'].map(function (d) {
            return dayLabels[d] + ':' + (am[d] || 'N');
        }).join(', ');
        var html = '<dl>';
        html += '<dt>Teacher</dt><dd>' + (data.teacher_name || '—') + '</dd>';
        html += '<dt>Office hours</dt><dd>' + (data.office_hours || '—') + '</dd>';
        html += '<dt>Lunch duty (not in room)</dt><dd>' + (data.lunch_duty || '—') + '</dd>';
        html += '<dt>Club meeting</dt><dd>' + (data.club_meeting || '—') + '</dd>';
        html += '<dt>Available (day : A/B)</dt><dd>' + availStr + '</dd>';
        html += '</dl>';
        detailBody.innerHTML = html;
        detailEl.hidden = false;
        if (btnFavorite && data.room_id) {
            btnFavorite.setAttribute('data-room-id', data.room_id);
            var isFav = favorites.indexOf(data.room_id) !== -1;
            btnFavorite.textContent = isFav ? '★ Favorited' : '☆ Favorite';
            btnFavorite.dataset.fav = isFav ? '1' : '0';
        }
    }

    function hideDetail() {
        if (detailEl) detailEl.hidden = true;
    }

    if (closeBtn) closeBtn.addEventListener('click', hideDetail);

    function buildView(filteredRooms) {
        var sortOption = sortBy ? sortBy.value : 'room';
        var roomNumbers = sortRoomNumbers(Object.keys(filteredRooms), sortOption);

        if (mapEl) {
            mapEl.innerHTML = '';
            roomNumbers.forEach(function (roomNum) {
                var data = filteredRooms[roomNum];
                var cell = document.createElement('button');
                cell.type = 'button';
                cell.className = 'room-cell open';
                cell.textContent = roomNum;
                cell.id = 'room-' + roomNum;
                cell.setAttribute('aria-label', 'Room ' + roomNum + '. Click for details.');
                cell.addEventListener('click', function () { showDetail(roomNum, data); });
                mapEl.appendChild(cell);
            });
        }

        if (listEl) {
            listEl.innerHTML = '';
            if (roomNumbers.length === 0) {
                listEl.innerHTML = '<li class="empty-list">No rooms match the selected filters.</li>';
                if (emptyState) {
                    emptyState.hidden = false;
                    emptyState.querySelector('p').textContent = 'No rooms match the current filters.';
                }
            } else {
                if (emptyState) emptyState.hidden = true;
                roomNumbers.forEach(function (roomNum) {
                    var data = filteredRooms[roomNum];
                    var li = document.createElement('li');
                    var a = document.createElement('a');
                    a.href = '#room-' + roomNum;
                    a.className = 'room-cell-link open';
                    a.textContent = 'Room ' + roomNum + ' — ' + (data.teacher_name || '');
                    a.addEventListener('click', function (e) {
                        e.preventDefault();
                        showDetail(roomNum, data);
                    });
                    li.appendChild(a);
                    listEl.appendChild(li);
                });
            }
        }
    }

    function runFilter() {
        var filtered = applyFiltersAndSearch();
        buildView(filtered);
    }

    setTodayBanner();
    runFilter();

    if (filterApply) filterApply.addEventListener('click', function (e) { e.preventDefault(); runFilter(); });
    if (filterClear) {
        filterClear.addEventListener('click', function (e) {
            e.preventDefault();
            if (filterDay) filterDay.value = 'ALL';
            if (filterLunch) filterLunch.value = 'ANY';
            if (searchInput) searchInput.value = '';
            buildView(rooms);
            if (emptyState) emptyState.hidden = true;
        });
    }
    if (emptyStateClear) {
        emptyStateClear.addEventListener('click', function () {
            if (filterDay) filterDay.value = 'ALL';
            if (filterLunch) filterLunch.value = 'ANY';
            if (searchInput) searchInput.value = '';
            buildView(rooms);
            emptyState.hidden = true;
        });
    }
    if (sortBy) sortBy.addEventListener('change', runFilter);
    if (searchInput) {
        searchInput.addEventListener('input', function () { runFilter(); });
        searchInput.addEventListener('keyup', function (e) { if (e.key === 'Enter') runFilter(); });
    }

    if (btnFavorite) {
        btnFavorite.addEventListener('click', function () {
            var roomId = btnFavorite.getAttribute('data-room-id');
            if (!roomId) return;
            var isFav = btnFavorite.dataset.fav === '1';
            var method = isFav ? 'DELETE' : 'POST';
            var body = new FormData();
            body.append('room_id', roomId);
            fetch('/api/favorites', { method: method, body: body, headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(function () {
                    if (isFav) favorites = favorites.filter(function (id) { return id !== parseInt(roomId, 10); });
                    else favorites.push(parseInt(roomId, 10));
                    btnFavorite.dataset.fav = isFav ? '0' : '1';
                    btnFavorite.textContent = isFav ? '☆ Favorite' : '★ Favorited';
                })
                .catch(function () {});
        });
    }
})();
