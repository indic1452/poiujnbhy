/* Интерфейс инженера: одностраничное приложение на ванильном JS (ES2020).
 *
 * Сборщиков нет, внешних загрузок нет — файл подключается тегом <script defer>
 * и работает в изолированном контуре. Разделы файла:
 *
 *   1. утилиты и словари;
 *   2. клиент REST API;
 *   3. общие элементы: уведомления, модальные окна, индикатор длинных операций;
 *   4. шапка, темы, маршрутизация;
 *   5. экран «Кейсы»;
 *   6. экран «Кейс» — три панели (факты | отчёт | источники и замечания);
 *   7. экран «Библиотека»;
 *   8. экран «Метрики» и журнал действий;
 *   9. запуск.
 */

'use strict';

(function () {

    // =====================================================================
    // 1. Утилиты и словари
    // =====================================================================

    const CASE_STATUS = {
        new: 'новый',
        draft: 'черновик',
        review: 'на проверке',
        approved: 'утверждён',
        archived: 'в архиве',
    };

    const REPORT_STATUS = {
        draft: 'черновик',
        verified: 'проверен',
        approved: 'утверждён',
    };

    const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'];

    const SEVERITY_LABEL = {
        info: 'информация',
        low: 'низкая',
        medium: 'средняя',
        high: 'высокая',
        critical: 'критическая',
    };

    const DOC_TYPE_LABEL = {
        literature: 'литература',
        standards: 'стандарты',
        datasheets: 'даташиты',
        reports: 'отчёты',
        regulations: 'регламенты',
    };

    const CONFIDENTIALITY_LABEL = {
        public: 'открыто',
        internal: 'для внутреннего пользования',
        nda: 'по соглашению о конфиденциальности',
    };

    const LEVEL_LABEL = {
        error: 'Ошибки',
        warning: 'Предупреждения',
        info: 'Замечания',
    };

    const AUDIT_LABEL = {
        'auth.login': 'вход в систему',
        'auth.fail': 'неудачная попытка входа',
        'case.create': 'создан кейс',
        'case.delete': 'удалён кейс',
        'case.facts.update': 'изменён факт-пакет',
        'report.generate': 'сгенерирован отчёт',
        'report.section.save': 'сохранена секция',
        'report.section.regenerate': 'перегенерирована секция',
        'report.section.restore': 'возвращён черновик модели',
        'report.approve': 'отчёт утверждён',
        'report.export': 'экспорт отчёта',
        'library.upload': 'загружен документ',
        'library.reindex': 'переиндексация библиотеки',
        'library.delete': 'удалён документ',
        'user.create': 'создан пользователь',
    };

    function $(selector, root) {
        return (root || document).querySelector(selector);
    }

    function $$(selector, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(selector));
    }

    /** Создание узла: h('div', {class: 'x', onclick: fn}, 'текст', node, [nodes]). */
    function h(tag, props) {
        const node = document.createElement(tag);
        const options = props || {};
        for (const key of Object.keys(options)) {
            const value = options[key];
            if (value === null || value === undefined || value === false) continue;
            if (key === 'class') node.className = value;
            else if (key === 'html') node.innerHTML = value;
            else if (key === 'dataset') Object.assign(node.dataset, value);
            else if (key === 'style') Object.assign(node.style, value);
            else if (key === 'value') node.value = value;
            else if (key === 'checked') node.checked = !!value;
            else if (key.slice(0, 2) === 'on' && typeof value === 'function') {
                node.addEventListener(key.slice(2), value);
            } else if (value === true) node.setAttribute(key, '');
            else node.setAttribute(key, value);
        }
        append(node, Array.prototype.slice.call(arguments, 2));
        return node;
    }

    function append(parent, children) {
        for (const child of children) {
            if (child === null || child === undefined || child === false) continue;
            if (Array.isArray(child)) append(parent, child);
            else if (child instanceof Node) parent.appendChild(child);
            else parent.appendChild(document.createTextNode(String(child)));
        }
        return parent;
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
        return node;
    }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function plural(count, one, few, many) {
        const mod10 = count % 10;
        const mod100 = count % 100;
        if (mod10 === 1 && mod100 !== 11) return one;
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
        return many;
    }

    function fmtDateTime(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (isNaN(date.getTime())) return String(value).replace('T', ' ').slice(0, 16);
        const pad = (n) => String(n).padStart(2, '0');
        return pad(date.getDate()) + '.' + pad(date.getMonth() + 1) + '.' + date.getFullYear() +
            ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }

    function fmtNumber(value, digits) {
        const number = Number(value);
        if (!isFinite(number)) return '—';
        return number.toFixed(digits === undefined ? 0 : digits).replace('.', ',');
    }

    function fmtBytes(bytes) {
        const units = ['Б', 'КБ', 'МБ', 'ГБ'];
        let value = Number(bytes) || 0;
        let index = 0;
        while (value >= 1024 && index < units.length - 1) {
            value /= 1024;
            index += 1;
        }
        return (index === 0 ? value.toFixed(0) : value.toFixed(1).replace('.', ',')) + ' ' + units[index];
    }

    function clone(value) {
        return value === undefined ? value : JSON.parse(JSON.stringify(value));
    }

    function debounce(fn, ms) {
        let timer = null;
        return function () {
            const args = arguments;
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(null, args), ms);
        };
    }

    function encodePath(value) {
        return String(value).split('/').map(encodeURIComponent).join('/');
    }

    function domId(prefix, value) {
        return prefix + String(value).replace(/[^\w-]/g, '_');
    }

    function normalizeTitle(value) {
        return String(value || '').replace(/^\s*\d+[.)]\s*/, '').trim().toLowerCase();
    }

    // =====================================================================
    // 2. Клиент REST API
    // =====================================================================

    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.name = 'ApiError';
            this.status = status;
        }
    }

    let leavingToLogin = false;

    function goToLogin() {
        if (leavingToLogin) return;
        leavingToLogin = true;
        const next = encodeURIComponent(location.hash || '#/cases');
        location.href = '/login?next=' + next;
    }

    async function request(path, options) {
        const opts = options || {};
        const init = {
            method: opts.method || 'GET',
            credentials: 'same-origin',
            headers: Object.assign({ 'Accept': 'application/json' }, opts.headers || {}),
        };
        if (opts.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.body);
        }

        let response;
        try {
            response = await fetch(path, init);
        } catch (error) {
            throw new ApiError(0, 'сервер недоступен: проверьте, запущен ли reportgen');
        }

        if (response.status === 401) {
            goToLogin();
            throw new ApiError(401, 'требуется вход в систему');
        }

        if (!response.ok) {
            let message = 'ошибка сервера (код ' + response.status + ')';
            try {
                const data = await response.json();
                if (data && data.error) message = data.error;
            } catch (error) {
                /* тело не JSON — оставляем сообщение по коду */
            }
            throw new ApiError(response.status, message);
        }

        if (opts.expect === 'blob') {
            return { blob: await response.blob(), filename: filenameFrom(response) };
        }
        if (response.status === 204) return null;
        return response.json();
    }

    function filenameFrom(response) {
        const header = response.headers.get('Content-Disposition') || '';
        const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
        if (utf8) {
            try {
                return decodeURIComponent(utf8[1]);
            } catch (error) {
                /* игнорируем и пробуем простой вариант */
            }
        }
        const simple = /filename="?([^";]+)"?/i.exec(header);
        return simple ? simple[1] : 'report.docx';
    }

    const api = {
        get: (path) => request(path),
        post: (path, body) => request(path, { method: 'POST', body: body || {} }),
        put: (path, body) => request(path, { method: 'PUT', body: body || {} }),
        del: (path) => request(path, { method: 'DELETE' }),
        download: (path) => request(path, { expect: 'blob' }),
    };

    /** Загрузка файла с индикатором прогресса (fetch прогресса отдачи не даёт). */
    function uploadFile(path, formData, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', path, true);
            xhr.withCredentials = true;
            xhr.setRequestHeader('Accept', 'application/json');
            xhr.upload.addEventListener('progress', (event) => {
                if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
            });
            xhr.addEventListener('load', () => {
                let data = null;
                try {
                    data = JSON.parse(xhr.responseText);
                } catch (error) {
                    data = null;
                }
                if (xhr.status === 401) {
                    goToLogin();
                    reject(new ApiError(401, 'требуется вход в систему'));
                    return;
                }
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(data || {});
                    return;
                }
                reject(new ApiError(xhr.status, (data && data.error) ||
                    ('не удалось загрузить файл (код ' + xhr.status + ')')));
            });
            xhr.addEventListener('error', () => reject(new ApiError(0, 'сеть недоступна при загрузке файла')));
            xhr.addEventListener('abort', () => reject(new ApiError(0, 'загрузка прервана')));
            xhr.send(formData);
        });
    }

    function errorText(error) {
        if (!error) return 'неизвестная ошибка';
        if (error instanceof ApiError) return error.message;
        return error.message || String(error);
    }

    // =====================================================================
    // 3. Уведомления, модальные окна, индикатор длительных операций
    // =====================================================================

    function toast(message, kind, ttl) {
        const box = $('#toasts');
        if (!box) return;
        const node = h('div', { class: 'toast' + (kind ? ' is-' + kind : '') },
            h('div', {}, message),
            h('button', { class: 'close', title: 'Закрыть', onclick: () => node.remove() }, '×'));
        box.appendChild(node);
        const lifetime = ttl || (kind === 'error' ? 12000 : 5000);
        setTimeout(() => node.remove(), lifetime);
    }

    function toastError(error) {
        toast(errorText(error), 'error');
    }

    /** Модальное окно. Возвращает объект с методом close(). */
    function openModal(options) {
        const root = $('#modal-root');
        const footer = h('footer', {});
        const body = h('div', { class: 'modal-body' });
        const modal = h('div', { class: 'modal' + (options.narrow ? ' modal--narrow' : '') },
            h('header', {},
                options.title,
                h('button', { class: 'btn btn--ghost btn--icon', title: 'Закрыть', onclick: () => close() }, '×')),
            body, footer);
        const backdrop = h('div', { class: 'modal-backdrop' }, modal);

        append(body, [options.body]);
        append(footer, [options.footer]);

        function onKey(event) {
            if (event.key === 'Escape') {
                event.stopPropagation();
                close();
            }
        }

        function close() {
            document.removeEventListener('keydown', onKey, true);
            backdrop.remove();
            if (options.onClose) options.onClose();
        }

        backdrop.addEventListener('mousedown', (event) => {
            if (event.target === backdrop && options.closeOnBackdrop !== false) close();
        });
        document.addEventListener('keydown', onKey, true);
        root.appendChild(backdrop);
        if (options.focus) setTimeout(() => { const node = $(options.focus, modal); if (node) node.focus(); }, 30);
        return { close: close, modal: modal, body: body, footer: footer };
    }

    /** Подтверждение действия. Возвращает Promise<boolean>. */
    function confirmDialog(options) {
        return new Promise((resolve) => {
            let answered = false;
            const finish = (value) => {
                if (answered) return;
                answered = true;
                dialog.close();
                resolve(value);
            };
            const confirmButton = h('button', {
                class: 'btn ' + (options.danger ? 'btn--danger' : 'btn--primary'),
                onclick: () => finish(true),
            }, options.confirmText || 'Подтвердить');

            const dialog = openModal({
                narrow: true,
                title: options.title || 'Подтверждение',
                body: h('div', {}, options.message ? h('div', {}, options.message) : null,
                    options.note ? h('div', { class: 'small muted', style: { marginTop: '8px' } }, options.note) : null),
                footer: [
                    h('button', { class: 'btn', onclick: () => finish(false) }, options.cancelText || 'Отмена'),
                    confirmButton,
                ],
                onClose: () => finish(false),
            });
            setTimeout(() => confirmButton.focus(), 30);
        });
    }

    let overlayTimer = null;

    function showOverlay(what, note) {
        const overlay = $('#overlay');
        $('#overlay-what').textContent = what;
        $('#overlay-note').textContent = note || '';
        const started = Date.now();
        const elapsed = $('#overlay-elapsed');
        const tick = () => {
            const seconds = Math.round((Date.now() - started) / 1000);
            const mm = Math.floor(seconds / 60);
            const ss = seconds % 60;
            elapsed.textContent = 'прошло ' + mm + ':' + String(ss).padStart(2, '0');
        };
        tick();
        clearInterval(overlayTimer);
        overlayTimer = setInterval(tick, 1000);
        overlay.hidden = false;
    }

    function hideOverlay() {
        clearInterval(overlayTimer);
        overlayTimer = null;
        $('#overlay').hidden = true;
    }

    async function withOverlay(what, note, fn) {
        showOverlay(what, note);
        try {
            return await fn();
        } finally {
            hideOverlay();
        }
    }

    // =====================================================================
    // 4. Состояние, шапка, темы, маршрутизация
    // =====================================================================

    const state = {
        user: null,
        authEnabled: true,
        config: { outlines: [], doc_types: [], confidentiality: ['public', 'internal', 'nda'], llm: {} },
        route: { name: 'cases', id: null },
    };

    function canEdit() {
        return !!state.user && (state.user.role === 'engineer' || state.user.role === 'admin');
    }

    function isAdmin() {
        return !!state.user && state.user.role === 'admin';
    }

    function outlineFor(reportType) {
        return (state.config.outlines || []).find((item) => item.report_type === reportType) || null;
    }

    function reportTypeTitle(reportType) {
        const outline = outlineFor(reportType);
        return outline ? outline.title : reportType;
    }

    function docTypeLabel(value) {
        return DOC_TYPE_LABEL[value] || value;
    }

    // -- темы ---------------------------------------------------------------

    const THEME_LABEL = { auto: 'авто', light: 'светлая', dark: 'тёмная' };

    function storageGet(key, fallback) {
        try {
            const value = localStorage.getItem(key);
            return value === null ? fallback : value;
        } catch (error) {
            return fallback;
        }
    }

    function storageSet(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (error) {
            /* приватный режим браузера — молча продолжаем */
        }
    }

    function applyTheme(mode) {
        const root = document.documentElement;
        if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
        else root.removeAttribute('data-theme');
        const button = $('#theme-btn');
        if (button) button.textContent = 'Тема: ' + (THEME_LABEL[mode] || 'авто');
    }

    function cycleTheme() {
        const order = ['auto', 'light', 'dark'];
        const current = storageGet('rg-theme', 'auto');
        const next = order[(order.indexOf(current) + 1) % order.length];
        storageSet('rg-theme', next);
        applyTheme(next);
    }

    // -- шапка --------------------------------------------------------------

    function renderChrome() {
        const brand = (state.config && state.config.brand) || null;
        if (brand && brand.name) {
            const label = $('.brand span');
            if (label) label.textContent = brand.name;
            document.title = brand.name;
        }
        if (brand && typeof brand.accent === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(brand.accent)) {
            document.documentElement.style.setProperty('--accent', brand.accent);
        }

        const llm = state.config.llm || {};
        const llmInfo = $('#llm-info');
        if (llmInfo) {
            llmInfo.textContent = llm.model ? 'модель: ' + llm.model : '';
            llmInfo.title = llm.base_url ? 'сервер модели: ' + llm.base_url : '';
        }

        const chip = $('#user-chip');
        const logout = $('#logout-btn');
        if (state.user) {
            chip.hidden = false;
            $('#user-name').textContent = state.user.full_name || state.user.login;
            $('#user-role').textContent = roleLabel(state.user.role) +
                (state.authEnabled ? '' : ' · локальный режим');
        } else {
            chip.hidden = true;
        }
        logout.hidden = !state.authEnabled;
        logout.onclick = async () => {
            try {
                await api.post('/api/auth/logout', {});
            } catch (error) {
                /* выходим в любом случае */
            }
            location.href = '/login';
        };

        const themeButton = $('#theme-btn');
        themeButton.onclick = cycleTheme;
    }

    function roleLabel(role) {
        return { viewer: 'наблюдатель', engineer: 'инженер', admin: 'администратор' }[role] || role;
    }

    function setActiveNav(name) {
        $$('#nav a').forEach((link) => {
            const route = link.dataset.route;
            link.classList.toggle('is-active', route === name || (name === 'case' && route === 'cases'));
        });
    }

    // -- маршрутизация ------------------------------------------------------

    let currentHash = '#/cases';
    let restoringHash = false;

    function parseHash(hash) {
        const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean);
        if (!parts.length) return { name: 'cases', id: null };
        if (parts[0] === 'case' && parts[1]) return { name: 'case', id: parts[1] };
        if (['cases', 'library', 'stats'].indexOf(parts[0]) !== -1) return { name: parts[0], id: null };
        return { name: 'cases', id: null };
    }

    function hasUnsaved() {
        return state.route.name === 'case' && (wb.dirty.size > 0 || wb.factsDirty);
    }

    function unsavedMessage() {
        const parts = [];
        if (wb.dirty.size) {
            parts.push('несохранённых секций: ' + wb.dirty.size);
        }
        if (wb.factsDirty) parts.push('изменён факт-пакет');
        return parts.join(', ');
    }

    async function onHashChange() {
        if (restoringHash) {
            restoringHash = false;
            return;
        }
        const next = location.hash || '#/cases';
        if (hasUnsaved()) {
            const ok = await confirmDialog({
                title: 'Есть несохранённые правки',
                message: 'В кейсе остались несохранённые изменения (' + unsavedMessage() +
                    '). Уйти со страницы и потерять их?',
                confirmText: 'Уйти без сохранения',
                danger: true,
            });
            if (!ok) {
                restoringHash = true;
                location.hash = currentHash;
                return;
            }
            wb.dirty.clear();
            wb.factsDirty = false;
        }
        currentHash = next;
        await renderRoute(parseHash(next));
    }

    async function renderRoute(route) {
        state.route = route;
        setActiveNav(route.name);
        const view = $('#view');
        clear(view);
        view.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка…'));
        try {
            if (route.name === 'cases') await renderCases(view);
            else if (route.name === 'case') await renderCase(view, route.id);
            else if (route.name === 'library') await renderLibrary(view);
            else if (route.name === 'stats') await renderStats(view);
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) return;
            clear(view);
            view.appendChild(h('div', { class: 'page' },
                h('div', { class: 'card card-pad' },
                    h('h3', { style: { color: 'var(--danger)', marginBottom: '6px' } }, 'Не удалось открыть раздел'),
                    h('div', { class: 'muted' }, errorText(error)),
                    h('div', { class: 'btn-row', style: { marginTop: '12px' } },
                        h('button', { class: 'btn', onclick: () => renderRoute(state.route) }, 'Повторить'),
                        h('a', { class: 'btn', href: '#/cases' }, 'К списку кейсов')))));
        }
    }

    function navigate(hash) {
        if (location.hash === hash) onHashChange();
        else location.hash = hash;
    }

    // =====================================================================
    // 5. Экран «Кейсы»
    // =====================================================================

    const casesState = { status: '', query: '', limit: 100, offset: 0, total: 0, items: [] };

    async function renderCases(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        const statusSelect = h('select', {
            onchange: (event) => {
                casesState.status = event.target.value;
                casesState.offset = 0;
                loadCases();
            },
        }, h('option', { value: '' }, 'Все статусы'),
            Object.keys(CASE_STATUS).map((key) =>
                h('option', { value: key, selected: casesState.status === key }, CASE_STATUS[key])));

        const searchInput = h('input', {
            type: 'search',
            class: 'grow',
            placeholder: 'Поиск: обращение, заголовок, заказчик',
            value: casesState.query,
            oninput: debounce((event) => {
                casesState.query = event.target.value.trim().toLowerCase();
                renderCasesTable();
            }, 200),
        });

        const tableBox = h('div', { class: 'card' });
        const footer = h('div', { class: 'toolbar', style: { marginTop: '12px' } });

        append(page, [
            h('div', { class: 'page-head' },
                h('h1', {}, 'Кейсы'),
                h('button', {
                    class: 'btn', onclick: () => loadCases(),
                }, 'Обновить'),
                canEdit() ? h('button', {
                    class: 'btn btn--primary', onclick: () => openNewCaseDialog(),
                }, '+ Новый кейс') : null),
            h('div', { class: 'toolbar' }, statusSelect, searchInput),
            tableBox,
            footer,
        ]);

        async function loadCases() {
            clear(tableBox);
            tableBox.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка кейсов…'));
            try {
                const query = '/api/cases?limit=' + casesState.limit + '&offset=' + casesState.offset +
                    (casesState.status ? '&status=' + encodeURIComponent(casesState.status) : '');
                const data = await api.get(query);
                casesState.items = data.items || [];
                casesState.total = data.total || 0;
                renderCasesTable();
            } catch (error) {
                clear(tableBox);
                tableBox.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        function renderCasesTable() {
            const query = casesState.query;
            const items = !query ? casesState.items : casesState.items.filter((item) => {
                const haystack = [item.case_id, item.title, item.customer, item.report_type]
                    .join(' ').toLowerCase();
                return haystack.indexOf(query) !== -1;
            });

            clear(tableBox);
            if (!items.length) {
                tableBox.appendChild(h('div', { class: 'empty' },
                    h('h3', {}, casesState.items.length ? 'Ничего не найдено' : 'Кейсов пока нет'),
                    h('div', {}, casesState.items.length
                        ? 'Измените строку поиска или фильтр статуса.'
                        : 'Создайте кейс: тип отчёта и факт-пакет из слоя анализа.')));
            } else {
                const body = h('tbody', {});
                items.forEach((item) => {
                    body.appendChild(h('tr', {
                        class: 'clickable',
                        onclick: (event) => {
                            if (event.target.closest('button')) return;
                            navigate('#/case/' + item.id);
                        },
                    },
                        h('td', { class: 'mono nowrap' }, item.case_id),
                        h('td', {}, item.title || h('span', { class: 'faint' }, '—')),
                        h('td', { class: 'small muted' }, reportTypeTitle(item.report_type)),
                        h('td', { class: 'small' }, item.customer || '—'),
                        h('td', {}, statusBadge(item.status)),
                        h('td', { class: 'small muted nowrap' }, fmtDateTime(item.updated_at)),
                        h('td', { class: 'nowrap' }, isAdmin() ? h('button', {
                            class: 'btn btn--icon btn--danger',
                            title: 'Удалить кейс',
                            onclick: () => deleteCase(item, loadCases),
                        }, '×') : null)));
                });
                tableBox.appendChild(h('div', { class: 'table-scroll' },
                    h('table', { class: 'grid' },
                        h('thead', {}, h('tr', {},
                            h('th', {}, 'Обращение'),
                            h('th', {}, 'Заголовок'),
                            h('th', {}, 'Тип отчёта'),
                            h('th', {}, 'Заказчик'),
                            h('th', {}, 'Статус'),
                            h('th', {}, 'Изменён'),
                            h('th', {}))),
                        body)));
            }

            clear(footer);
            const from = casesState.total ? casesState.offset + 1 : 0;
            const to = casesState.offset + casesState.items.length;
            append(footer, [
                h('span', { class: 'small muted' },
                    'показаны ' + from + '–' + to + ' из ' + casesState.total +
                    (query ? ' · фильтр применён к загруженной странице' : '')),
                h('span', { class: 'grow' }),
                h('button', {
                    class: 'btn btn--sm', disabled: casesState.offset <= 0,
                    onclick: () => {
                        casesState.offset = Math.max(0, casesState.offset - casesState.limit);
                        loadCases();
                    },
                }, '← Предыдущие'),
                h('button', {
                    class: 'btn btn--sm', disabled: to >= casesState.total,
                    onclick: () => {
                        casesState.offset += casesState.limit;
                        loadCases();
                    },
                }, 'Следующие →'),
            ]);
        }

        await loadCases();
    }

    function statusBadge(status) {
        const kind = { approved: 'ok', review: 'warn', archived: '', draft: 'info', new: 'accent' }[status];
        return h('span', { class: 'badge' + (kind ? ' badge--' + kind : '') }, CASE_STATUS[status] || status);
    }

    async function deleteCase(item, after) {
        const ok = await confirmDialog({
            title: 'Удалить кейс',
            message: 'Кейс ' + item.case_id + ' будет удалён вместе со всеми версиями отчёта. ' +
                'Действие необратимо.',
            note: 'Сохранённые пары «черновик → финал» в обучающем наборе не удаляются.',
            confirmText: 'Удалить',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.del('/api/cases/' + item.id);
            toast('Кейс ' + item.case_id + ' удалён', 'ok');
            if (after) after();
        } catch (error) {
            toastError(error);
        }
    }

    // -- модальное окно «Новый кейс» ----------------------------------------

    function factsSkeleton(outline, caseId) {
        const keys = [];
        (outline ? outline.sections : []).forEach((section) => {
            (section.required_facts || []).forEach((key) => {
                if (keys.indexOf(key) === -1) keys.push(key);
            });
        });
        const measurements = {};
        keys.forEach((key) => {
            measurements[key] = { title: key, value: '', unit: '', method: '', uncertainty: '' };
        });
        return {
            case_id: caseId || '',
            report_type: outline ? outline.report_type : '',
            customer: '',
            request: '',
            equipment: {},
            keywords: [],
            artifacts: [],
            measurements: measurements,
            findings: [],
            timeline: [],
        };
    }

    function openNewCaseDialog() {
        const outlines = state.config.outlines || [];
        if (!outlines.length) {
            toast('Нет ни одного шаблона-плана: положите файл templates/outline_<тип>.json', 'error');
            return;
        }

        const typeSelect = h('select', {
            onchange: () => {
                if (!jsonTouched) fillSkeleton();
            },
        }, outlines.map((outline) =>
            h('option', { value: outline.report_type }, outline.title + ' (' + outline.report_type + ')')));

        const caseInput = h('input', {
            type: 'text', placeholder: 'например, SUP-2024-118', class: 'mono',
            oninput: () => {
                if (!jsonTouched) fillSkeleton();
            },
        });
        const titleInput = h('input', { type: 'text', placeholder: 'краткий заголовок кейса (необязательно)' });

        const status = h('div', { class: 'json-status' });
        const editor = h('textarea', {
            class: 'json-editor', spellcheck: 'false',
            oninput: () => {
                jsonTouched = true;
                validate();
            },
        });
        const fileInput = h('input', {
            type: 'file', accept: '.json,application/json', style: { display: 'none' },
            onchange: (event) => {
                const file = event.target.files && event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => {
                    editor.value = String(reader.result || '');
                    jsonTouched = true;
                    validate();
                    const parsed = validate();
                    if (parsed) {
                        if (parsed.case_id) caseInput.value = parsed.case_id;
                        if (parsed.report_type) {
                            const known = outlines.some((o) => o.report_type === parsed.report_type);
                            if (known) typeSelect.value = parsed.report_type;
                            else toast('В файле указан тип отчёта «' + parsed.report_type +
                                '», для которого нет шаблона-плана', 'error');
                        }
                    }
                };
                reader.onerror = () => toast('не удалось прочитать файл', 'error');
                reader.readAsText(file, 'utf-8');
                event.target.value = '';
            },
        });

        let jsonTouched = false;

        function fillSkeleton() {
            const outline = outlines.find((item) => item.report_type === typeSelect.value);
            editor.value = JSON.stringify(factsSkeleton(outline, caseInput.value.trim()), null, 2);
            validate();
        }

        function validate() {
            const raw = editor.value.trim();
            if (!raw) {
                status.className = 'json-status is-bad';
                status.textContent = 'факт-пакет пуст';
                return null;
            }
            try {
                const parsed = JSON.parse(raw);
                if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                    status.className = 'json-status is-bad';
                    status.textContent = 'ожидался объект JSON верхнего уровня';
                    return null;
                }
                const count = Object.keys(parsed.measurements || {}).length;
                status.className = 'json-status is-ok';
                status.textContent = 'JSON корректен · измерений: ' + count +
                    ' · находок: ' + ((parsed.findings || []).length);
                return parsed;
            } catch (error) {
                status.className = 'json-status is-bad';
                status.textContent = 'ошибка синтаксиса JSON: ' + error.message;
                return null;
            }
        }

        const createButton = h('button', { class: 'btn btn--primary', onclick: submit }, 'Создать кейс');

        const dialog = openModal({
            title: 'Новый кейс',
            body: [
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Тип отчёта', typeSelect),
                    h('label', { class: 'field' }, 'Идентификатор обращения', caseInput)),
                h('label', { class: 'field' }, 'Заголовок', titleInput),
                h('div', {},
                    h('div', { class: 'toolbar', style: { marginBottom: '6px' } },
                        h('span', { class: 'small muted grow' },
                            'Факт-пакет (JSON) — единственный источник чисел для отчёта'),
                        h('button', { class: 'btn btn--sm', onclick: () => fileInput.click() }, 'Загрузить из файла'),
                        h('button', {
                            class: 'btn btn--sm',
                            title: 'Заполнить заготовку обязательными ключами выбранного шаблона',
                            onclick: () => { jsonTouched = false; fillSkeleton(); },
                        }, 'Заготовка по шаблону'),
                        fileInput),
                    editor, status),
            ],
            footer: [
                h('span', { class: 'small faint spacer' }, 'Поля проверяются сервером по схеме факт-пакета'),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                createButton,
            ],
            focus: 'input',
        });

        fillSkeleton();

        async function submit() {
            const facts = validate();
            if (!facts) {
                toast('Исправьте факт-пакет: ' + status.textContent, 'error');
                return;
            }
            const caseId = caseInput.value.trim() || String(facts.case_id || '').trim();
            if (!caseId) {
                toast('Укажите идентификатор обращения', 'error');
                caseInput.focus();
                return;
            }
            const reportType = typeSelect.value;
            facts.case_id = caseId;
            facts.report_type = reportType;

            createButton.disabled = true;
            createButton.textContent = 'Создание…';
            try {
                const data = await api.post('/api/cases', {
                    case_id: caseId,
                    report_type: reportType,
                    title: titleInput.value.trim(),
                    facts: facts,
                });
                dialog.close();
                toast('Кейс ' + caseId + ' создан', 'ok');
                navigate('#/case/' + data.case.id);
            } catch (error) {
                toastError(error);
            } finally {
                createButton.disabled = false;
                createButton.textContent = 'Создать кейс';
            }
        }
    }

    // =====================================================================
    // 6. Экран «Кейс»: три панели
    // =====================================================================

    const wb = {
        case: null,
        coverage: {},
        reports: [],
        report: null,
        facts: null,
        rows: [],
        findings: [],
        factsDirty: false,
        jsonMode: false,
        jsonText: '',
        drafts: new Map(),
        dirty: new Set(),
        tab: 'sources',
        activeSource: null,
        focused: null,
        busy: false,
        nodes: {},
    };

    function resetWorkbench() {
        wb.case = null;
        wb.coverage = {};
        wb.reports = [];
        wb.report = null;
        wb.facts = null;
        wb.rows = [];
        wb.findings = [];
        wb.factsDirty = false;
        wb.jsonMode = false;
        wb.jsonText = '';
        wb.drafts = new Map();
        wb.dirty = new Set();
        wb.tab = 'sources';
        wb.activeSource = null;
        wb.focused = null;
        wb.busy = false;
        wb.nodes = {};
    }

    async function renderCase(view, caseRef) {
        resetWorkbench();
        const data = await api.get('/api/cases/' + encodeURIComponent(caseRef));
        wb.case = data.case;
        wb.coverage = data.coverage || {};
        wb.reports = (data.reports || []).slice().sort((a, b) => a.version - b.version);
        wb.facts = clone(wb.case.facts || {});
        rebuildFactRows();

        const latest = wb.reports.length ? wb.reports[wb.reports.length - 1] : null;
        if (latest) {
            try {
                wb.report = await loadReport(latest.id);
            } catch (error) {
                toastError(error);
            }
        }

        clear(view);
        view.appendChild(buildWorkbench());
        refreshAll();
    }

    /** Загрузка версии отчёта вместе со списком источников для правой панели. */
    async function loadReport(reportId) {
        const payload = await api.get('/api/reports/' + reportId);
        const report = normalizeReport(payload.report);
        const needsSources = report && !report.sources.length &&
            report.sections.some((section) => (section.sources || []).length);
        if (needsSources) {
            // Отдельный маршрут на случай, если отчёт отдан без вложенных цитат.
            const extra = await api.get('/api/reports/' + reportId + '/sources');
            report.sources = extra.items || [];
        }
        return report;
    }

    function normalizeReport(report) {
        if (!report) return null;
        const copy = Object.assign({}, report);
        copy.sections = (report.sections || []).slice().sort((a, b) => (a.ord || 0) - (b.ord || 0));
        copy.issues = report.issues || [];
        copy.sources = report.sources || (report.meta && report.meta.sources) || [];
        if (copy.errors === undefined) {
            copy.errors = copy.issues.filter((issue) => issue.level === 'error').length;
        }
        if (copy.warnings === undefined) {
            copy.warnings = copy.issues.filter((issue) => issue.level === 'warning').length;
        }
        return copy;
    }

    function buildWorkbench() {
        const screen = h('div', {
            style: { flex: '1', display: 'flex', flexDirection: 'column', minHeight: '0' },
        });

        const workbench = h('div', { class: 'workbench', id: 'workbench', dataset: { panel: 'report' } });
        const switcher = h('div', { class: 'panel-switch' },
            ['facts', 'report', 'side'].map((key) => h('button', {
                class: 'btn btn--sm' + (key === 'report' ? ' btn--primary' : ''),
                dataset: { panel: key },
                onclick: (event) => {
                    workbench.dataset.panel = key;
                    $$('.panel-switch button', screen).forEach((button) => {
                        button.classList.toggle('btn--primary', button === event.currentTarget);
                    });
                },
            }, { facts: 'Факты', report: 'Отчёт', side: 'Источники и замечания' }[key])));

        const layout = {
            left: parseInt(storageGet('rg-left', '400'), 10) || 400,
            right: parseInt(storageGet('rg-right', '360'), 10) || 360,
        };
        workbench.style.setProperty('--left-w', layout.left + 'px');
        workbench.style.setProperty('--right-w', layout.right + 'px');

        const leftSplitter = h('div', { class: 'splitter', title: 'Потяните, чтобы изменить ширину панели' });
        const rightSplitter = h('div', { class: 'splitter', title: 'Потяните, чтобы изменить ширину панели' });
        bindSplitter(leftSplitter, workbench, 'left', layout);
        bindSplitter(rightSplitter, workbench, 'right', layout);

        append(workbench, [
            buildFactsPanel(),
            leftSplitter,
            buildReportPanel(),
            rightSplitter,
            buildSidePanel(),
        ]);
        append(screen, [switcher, workbench]);
        return screen;
    }

    function bindSplitter(splitter, workbench, which, layout) {
        splitter.addEventListener('mousedown', (event) => {
            event.preventDefault();
            const startX = event.clientX;
            const startWidth = layout[which];
            splitter.classList.add('is-dragging');

            function onMove(moveEvent) {
                const delta = moveEvent.clientX - startX;
                const raw = which === 'left' ? startWidth + delta : startWidth - delta;
                const value = Math.min(900, Math.max(280, Math.round(raw)));
                layout[which] = value;
                workbench.style.setProperty(which === 'left' ? '--left-w' : '--right-w', value + 'px');
            }

            function onUp() {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                splitter.classList.remove('is-dragging');
                storageSet(which === 'left' ? 'rg-left' : 'rg-right', String(layout[which]));
                $$('.editor').forEach(autosize);
                $$('.section-card').forEach(syncBackdrop);
            }

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }

    // -- панель фактов ------------------------------------------------------

    const MEASUREMENT_FIELDS = ['title', 'value', 'unit', 'method', 'uncertainty', 'source', 'note'];

    function rebuildFactRows() {
        const measurements = (wb.facts && wb.facts.measurements) || {};
        wb.rows = Object.keys(measurements).map((key) => {
            const raw = measurements[key] || {};
            const row = { key: key };
            MEASUREMENT_FIELDS.forEach((field) => {
                row[field] = raw[field] === undefined || raw[field] === null ? '' : String(raw[field]);
            });
            return row;
        });
        wb.findings = ((wb.facts && wb.facts.findings) || []).map((finding) => ({
            id: finding.id || '',
            severity: finding.severity || 'info',
            title: finding.title || '',
            description: finding.description || '',
            evidence: (finding.evidence || []).join(', '),
            refs: (finding.refs || []).join(', '),
        }));
    }

    function parseValue(raw) {
        const text = String(raw === undefined || raw === null ? '' : raw).trim();
        if (text === '') return '';
        if (/^-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?$/.test(text)) {
            const number = Number(text.replace(',', '.'));
            if (isFinite(number)) return number;
        }
        return String(raw).trim();
    }

    function serializeFacts() {
        const facts = Object.assign({}, wb.facts || {});
        facts.case_id = wb.case.case_id;
        facts.report_type = wb.case.report_type;

        const measurements = {};
        wb.rows.forEach((row) => {
            const key = row.key.trim();
            if (!key) return;
            const item = { value: parseValue(row.value) };
            MEASUREMENT_FIELDS.forEach((field) => {
                if (field === 'value') return;
                const value = String(row[field] || '').trim();
                if (value) item[field] = value;
            });
            measurements[key] = item;
        });
        facts.measurements = measurements;

        facts.findings = wb.findings.map((finding) => {
            const item = {
                id: finding.id.trim(),
                severity: finding.severity,
                title: finding.title.trim(),
            };
            if (finding.description.trim()) item.description = finding.description.trim();
            const evidence = splitList(finding.evidence);
            if (evidence.length) item.evidence = evidence;
            const refs = splitList(finding.refs);
            if (refs.length) item.refs = refs;
            return item;
        });
        return facts;
    }

    function splitList(value) {
        return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
    }

    /** Проверки, повторяющие требования схемы факт-пакета (док. 08). */
    function validateFacts() {
        const problems = [];
        const seen = new Set();
        wb.rows.forEach((row, index) => {
            const key = row.key.trim();
            if (!key) problems.push('измерение №' + (index + 1) + ': не заполнен ключ');
            else if (seen.has(key)) problems.push('ключ измерения «' + key + '» повторяется');
            else seen.add(key);
        });
        wb.findings.forEach((finding, index) => {
            const number = 'находка №' + (index + 1);
            if (!finding.id.trim()) problems.push(number + ': не заполнен идентификатор');
            if (!finding.title.trim()) problems.push(number + ': не заполнена формулировка');
            if (SEVERITIES.indexOf(finding.severity) === -1) problems.push(number + ': неизвестная критичность');
            splitList(finding.evidence).forEach((key) => {
                if (!seen.has(key)) {
                    problems.push(number + ': ссылка на несуществующее измерение «' + key + '»');
                }
            });
        });
        return problems;
    }

    function localCoverage() {
        const outline = outlineFor(wb.case.report_type);
        const have = new Set(wb.rows.map((row) => row.key.trim()).filter(Boolean));
        if (!outline) {
            return Object.keys(wb.coverage || {}).map((sectionId) => ({
                id: sectionId, title: sectionId, keys: wb.coverage[sectionId] || [],
            }));
        }
        const result = [];
        outline.sections.forEach((section) => {
            const missing = (section.required_facts || []).filter((key) => !have.has(key));
            if (missing.length) result.push({ id: section.id, title: section.title, keys: missing });
        });
        return result;
    }

    function missingKeySet() {
        const keys = new Set();
        localCoverage().forEach((entry) => entry.keys.forEach((key) => keys.add(key)));
        return keys;
    }

    function buildFactsPanel() {
        const saveButton = h('button', {
            class: 'btn btn--primary btn--sm', disabled: true,
            title: 'Сохранить факт-пакет (Ctrl+S)',
            onclick: () => saveFacts(),
        }, 'Сохранить факты');

        const modeButton = h('button', {
            class: 'btn btn--sm',
            onclick: () => toggleJsonMode(),
        }, 'Показать JSON');

        const body = h('div', { class: 'panel-body' });
        const digest = h('div', { class: 'small faint' });

        wb.nodes.factsBody = body;
        wb.nodes.factsSave = saveButton;
        wb.nodes.factsMode = modeButton;
        wb.nodes.factsDigest = digest;

        return h('section', { class: 'panel panel--facts' },
            h('div', { class: 'panel-head' },
                h('div', { class: 'panel-head-row' },
                    h('span', { class: 'panel-title' }, 'Факт-пакет'),
                    modeButton, saveButton),
                h('div', { class: 'panel-head-row' }, digest)),
            body);
    }

    function markFactsDirty() {
        wb.factsDirty = true;
        if (wb.nodes.factsSave) wb.nodes.factsSave.disabled = !canEdit();
        updateFactsDigest();
    }

    function updateFactsDigest() {
        if (!wb.nodes.factsDigest) return;
        const parts = ['хеш: ' + (wb.case.facts_digest || '—').slice(0, 12)];
        parts.push('измерений: ' + wb.rows.length);
        parts.push('находок: ' + wb.findings.length);
        clear(wb.nodes.factsDigest);
        append(wb.nodes.factsDigest, [
            parts.join(' · '),
            wb.factsDirty ? h('span', { class: 'badge badge--warn', style: { marginLeft: '6px' } }, 'не сохранено') : null,
        ]);
    }

    function toggleJsonMode() {
        if (wb.jsonMode) {
            let parsed;
            try {
                parsed = JSON.parse(wb.jsonText);
            } catch (error) {
                toast('Не переключаюсь в таблицу: ошибка синтаксиса JSON — ' + error.message, 'error');
                return;
            }
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                toast('Ожидался объект JSON верхнего уровня', 'error');
                return;
            }
            wb.facts = parsed;
            rebuildFactRows();
            wb.jsonMode = false;
            wb.nodes.factsMode.textContent = 'Показать JSON';
        } else {
            wb.facts = serializeFacts();
            wb.jsonText = JSON.stringify(wb.facts, null, 2);
            wb.jsonMode = true;
            wb.nodes.factsMode.textContent = 'Показать таблицу';
        }
        renderFactsBody();
    }

    function renderFactsBody() {
        const body = wb.nodes.factsBody;
        if (!body) return;
        const scroll = body.scrollTop;
        clear(body);
        if (wb.jsonMode) renderFactsJson(body);
        else renderFactsTable(body);
        body.scrollTop = scroll;
        updateFactsDigest();
    }

    function renderFactsJson(body) {
        const status = h('div', { class: 'json-status' });
        const editor = h('textarea', {
            class: 'json-editor', spellcheck: 'false', disabled: !canEdit(),
            oninput: (event) => {
                wb.jsonText = event.target.value;
                markFactsDirty();
                check();
            },
        });
        editor.value = wb.jsonText;

        function check() {
            try {
                const parsed = JSON.parse(wb.jsonText);
                const count = Object.keys((parsed && parsed.measurements) || {}).length;
                status.className = 'json-status is-ok';
                status.textContent = 'JSON корректен · измерений: ' + count;
                return true;
            } catch (error) {
                status.className = 'json-status is-bad';
                status.textContent = 'ошибка синтаксиса: ' + error.message;
                return false;
            }
        }

        append(body, [
            h('div', { class: 'small muted', style: { marginBottom: '8px' } },
                'Ручной режим: правьте весь факт-пакет как JSON. ' +
                'Идентификатор обращения и тип отчёта менять нельзя.'),
            editor, status,
        ]);
        check();
    }

    /** Блок «каких обязательных измерений не хватает» — красные ключи из coverage. */
    function buildCoverageBox() {
        const coverage = localCoverage();
        if (!coverage.length) {
            return h('div', { class: 'badge badge--ok', style: { marginBottom: '12px' } },
                'Обязательные измерения на месте');
        }
        const box = h('div', { class: 'coverage-box' },
            h('b', {}, 'Не хватает обязательных измерений'),
            h('div', { class: 'small muted' },
                'Шаблон отчёта требует эти ключи — без них секции будут помечены «не хватает данных».'));
        coverage.forEach((entry) => {
            box.appendChild(h('div', { class: 'coverage-line' },
                h('span', { class: 'small' }, entry.title + ': '),
                entry.keys.map((key) => h('button', {
                    class: 'chip', title: 'Добавить измерение «' + key + '» в таблицу',
                    onclick: () => addMeasurement(key),
                }, key, ' +'))));
        });
        return box;
    }

    function renderFactsTable(body) {
        const missing = missingKeySet();

        wb.nodes.coverageBox = buildCoverageBox();
        body.appendChild(wb.nodes.coverageBox);

        // -- общие сведения
        const general = h('div', { class: 'facts-section' },
            h('h4', {}, 'Общие сведения'),
            h('div', { class: 'kv' },
                h('dt', {}, 'обращение'), h('dd', { class: 'mono' }, wb.case.case_id),
                h('dt', {}, 'тип отчёта'), h('dd', {}, reportTypeTitle(wb.case.report_type)),
                h('dt', {}, 'статус'), h('dd', {}, CASE_STATUS[wb.case.status] || wb.case.status)),
            h('label', { class: 'field', style: { marginTop: '8px' } }, 'Заказчик (обезличенный)',
                h('input', {
                    type: 'text', value: wb.facts.customer || '', disabled: !canEdit(),
                    oninput: (event) => { wb.facts.customer = event.target.value; markFactsDirty(); },
                })),
            h('label', { class: 'field', style: { marginTop: '8px' } }, 'Суть обращения',
                h('textarea', {
                    rows: '3', disabled: !canEdit(),
                    oninput: (event) => { wb.facts.request = event.target.value; markFactsDirty(); },
                }, wb.facts.request || '')),
            h('label', { class: 'field', style: { marginTop: '8px' } }, 'Ключевые слова (через запятую)',
                h('input', {
                    type: 'text', value: (wb.facts.keywords || []).join(', '), disabled: !canEdit(),
                    oninput: (event) => {
                        wb.facts.keywords = splitList(event.target.value);
                        markFactsDirty();
                    },
                })));
        body.appendChild(general);

        // -- измерения
        const tbody = h('tbody', {});
        const detailed = storageGet('facts-detailed', '0') === '1';
        const headCells = detailed
            ? [h('th', { class: 'col-key' }, 'Ключ'), h('th', {}, 'Название'),
               h('th', {}, 'Значение'), h('th', { class: 'col-unit' }, 'Ед.'),
               h('th', {}, 'Метод'), h('th', {}, 'Погрешность'),
               h('th', { class: 'col-act' })]
            : [h('th', {}, 'Измерение и ключ'), h('th', { class: 'col-value' }, 'Значение'),
               h('th', { class: 'col-unit' }, 'Ед.'), h('th', { class: 'col-act' })];
        const table = h('table', { class: 'facts-table' + (detailed ? '' : ' is-compact') },
            h('thead', {}, h('tr', {}, ...headCells)),
            tbody);

        wb.rows.forEach((row) => tbody.appendChild(measurementRow(row, missing, detailed)));

        body.appendChild(h('div', { class: 'facts-section' },
            h('h4', {},
                'Измерения ', h('span', { class: 'faint' }, '(' + wb.rows.length + ')'),
                h('label', { class: 'facts-toggle', title: 'Показывать колонки «Метод» и «Погрешность»' },
                    h('input', {
                        type: 'checkbox', checked: detailed,
                        onchange: (event) => {
                            storageSet('facts-detailed', event.target.checked ? '1' : '0');
                            renderFactsBody();
                        },
                    }), ' подробно')),
            h('div', { class: 'table-scroll' }, table),
            canEdit() ? h('button', {
                class: 'btn btn--sm', style: { marginTop: '8px' },
                onclick: () => addMeasurement(''),
            }, '+ измерение') : null,
            h('div', { class: 'small faint', style: { marginTop: '6px' } },
                'Единица отдельно от значения, погрешность — там, где она есть (док. 08).')));

        // -- находки
        const findingsBox = h('div', {});
        wb.findings.forEach((finding, index) => findingsBox.appendChild(findingCard(finding, index)));
        body.appendChild(h('div', { class: 'facts-section' },
            h('h4', {}, 'Находки ', h('span', { class: 'faint' }, '(' + wb.findings.length + ')')),
            findingsBox,
            canEdit() ? h('button', {
                class: 'btn btn--sm',
                onclick: () => {
                    wb.findings.push({
                        id: 'F' + (wb.findings.length + 1), severity: 'medium',
                        title: '', description: '', evidence: '', refs: '',
                    });
                    markFactsDirty();
                    renderFactsBody();
                },
            }, '+ находка') : null));

        body.appendChild(h('div', { class: 'small faint' },
            'Поля equipment, artifacts и timeline правятся в режиме «Показать JSON».'));
    }

    function measurementRow(row, missing, detailed) {
        const tr = h('tr', {});
        const editable = canEdit();

        function field(name, className, placeholder) {
            const input = h('input', {
                type: 'text', class: className || '', value: row[name] || '',
                placeholder: placeholder || '', disabled: !editable,
                title: row[name] || '',
                oninput: (event) => {
                    row[name] = event.target.value;
                    // В узкой панели текст не помещается целиком — показываем его по наведению.
                    event.target.title = event.target.value;
                    if (name === 'key' || name === 'value') refreshCoverageMarks();
                    markFactsDirty();
                },
            });
            input.dataset.field = name;
            if (name === 'value' && !String(row.value || '').trim()) input.classList.add('is-bad');
            return input;
        }

        const remove = h('td', {}, editable ? h('button', {
            class: 'btn btn--icon btn--ghost', title: 'Удалить измерение',
            onclick: () => {
                const index = wb.rows.indexOf(row);
                if (index !== -1) wb.rows.splice(index, 1);
                markFactsDirty();
                renderFactsBody();
            },
        }, '×') : null);

        tr.dataset.key = row.key;
        if (missing.has(row.key)) tr.classList.add('is-missing');

        if (detailed) {
            append(tr, [
                h('td', {}, field('key', 'key', 'ключ')),
                h('td', {}, field('title', '', 'название')),
                h('td', {}, field('value', '', 'значение')),
                h('td', {}, field('unit', '', 'ед.')),
                h('td', {}, field('method', '', 'как получено')),
                h('td', {}, field('uncertainty', '', '±')),
                remove,
            ]);
        } else {
            // Компактный режим: название сверху, ключ мелким моноширинным снизу —
            // в панели шириной 400 px это единственная читаемая раскладка.
            append(tr, [
                h('td', { class: 'cell-stack' },
                    field('title', '', 'название измерения'),
                    field('key', 'key', 'ключ')),
                h('td', {}, field('value', '', 'значение')),
                h('td', {}, field('unit', '', 'ед.')),
                remove,
            ]);
        }
        return tr;
    }

    function refreshCoverageMarks() {
        // Обновляем только подсветку и блок покрытия: перерисовка таблицы
        // потеряла бы фокус в поле, которое сейчас правит инженер.
        const missing = missingKeySet();
        if (wb.nodes.coverageBox && wb.nodes.coverageBox.parentNode) {
            const fresh = buildCoverageBox();
            wb.nodes.coverageBox.replaceWith(fresh);
            wb.nodes.coverageBox = fresh;
        }
        $$('.facts-table tbody tr').forEach((tr, index) => {
            const row = wb.rows[index];
            if (!row) return;
            tr.classList.toggle('is-missing', missing.has(row.key.trim()));
            const valueInput = tr.querySelector('input[data-field="value"]');
            if (valueInput) valueInput.classList.toggle('is-bad', !String(row.value || '').trim());
        });
    }

    function addMeasurement(key) {
        wb.rows.push({ key: key || '', title: key || '', value: '', unit: '', method: '', uncertainty: '', source: '', note: '' });
        markFactsDirty();
        renderFactsBody();
        const inputs = $$('.facts-table tbody tr input.key');
        const last = inputs[inputs.length - 1];
        if (last) {
            last.focus();
            if (key) {
                const valueInput = last.closest('tr').children[2].firstChild;
                if (valueInput) valueInput.focus();
            }
        }
    }

    function findingCard(finding, index) {
        const editable = canEdit();
        return h('div', { class: 'finding' },
            h('div', { class: 'finding-head' },
                h('input', {
                    type: 'text', class: 'fid', value: finding.id, placeholder: 'F1', disabled: !editable,
                    oninput: (event) => { finding.id = event.target.value; markFactsDirty(); },
                }),
                h('select', {
                    disabled: !editable,
                    onchange: (event) => { finding.severity = event.target.value; markFactsDirty(); },
                }, SEVERITIES.map((level) => h('option', {
                    value: level, selected: finding.severity === level,
                }, SEVERITY_LABEL[level]))),
                h('span', { style: { flex: '1' } }),
                editable ? h('button', {
                    class: 'btn btn--icon btn--ghost', title: 'Удалить находку',
                    onclick: () => {
                        wb.findings.splice(index, 1);
                        markFactsDirty();
                        renderFactsBody();
                    },
                }, '×') : null),
            h('input', {
                type: 'text', value: finding.title, placeholder: 'формулировка одной строкой',
                disabled: !editable,
                oninput: (event) => { finding.title = event.target.value; markFactsDirty(); },
            }),
            h('textarea', {
                placeholder: 'что именно наблюдается', disabled: !editable,
                oninput: (event) => { finding.description = event.target.value; markFactsDirty(); },
            }, finding.description),
            h('input', {
                type: 'text', class: 'mono', value: finding.evidence, disabled: !editable,
                placeholder: 'подтверждающие измерения: snr, evm',
                oninput: (event) => { finding.evidence = event.target.value; markFactsDirty(); },
            }),
            h('input', {
                type: 'text', value: finding.refs, disabled: !editable,
                placeholder: 'нормативные ссылки через запятую',
                oninput: (event) => { finding.refs = event.target.value; markFactsDirty(); },
            }));
    }

    async function saveFacts() {
        if (!canEdit()) {
            toast('Недостаточно прав: правка фактов доступна инженеру', 'error');
            return;
        }
        let facts;
        if (wb.jsonMode) {
            try {
                facts = JSON.parse(wb.jsonText);
            } catch (error) {
                toast('Ошибка синтаксиса JSON: ' + error.message, 'error');
                return;
            }
        } else {
            const problems = validateFacts();
            if (problems.length) {
                toast('Факт-пакет не сохранён: ' + problems.slice(0, 3).join('; ') +
                    (problems.length > 3 ? ' и ещё ' + (problems.length - 3) : ''), 'error');
                return;
            }
            facts = serializeFacts();
        }

        const button = wb.nodes.factsSave;
        button.disabled = true;
        const label = button.textContent;
        button.textContent = 'Сохранение…';
        try {
            const data = await api.put('/api/cases/' + wb.case.id + '/facts', { facts: facts });
            wb.case = data.case;
            wb.coverage = data.coverage || {};
            wb.facts = clone(wb.case.facts || {});
            rebuildFactRows();
            if (wb.jsonMode) wb.jsonText = JSON.stringify(wb.facts, null, 2);
            wb.factsDirty = false;
            renderFactsBody();
            toast('Факт-пакет сохранён', 'ok');
            if (wb.report) {
                toast('Числа изменились — перепроверьте отчёт кнопкой «Проверить»', 'info');
            }
        } catch (error) {
            toastError(error);
        } finally {
            button.textContent = label;
            button.disabled = !wb.factsDirty;
        }
    }

    // -- центральная панель: отчёт -----------------------------------------

    function buildReportPanel() {
        const head = h('div', { class: 'report-head' });
        const body = h('div', { class: 'panel-body' });
        wb.nodes.reportHead = head;
        wb.nodes.reportBody = body;
        return h('section', { class: 'panel panel--report' }, head, body);
    }

    function renderReportHead() {
        const head = wb.nodes.reportHead;
        clear(head);
        const report = wb.report;
        const editable = canEdit();

        const versionSelect = h('select', {
            title: 'Версия отчёта',
            disabled: wb.reports.length < 2,
            onchange: (event) => switchVersion(Number(event.target.value)),
        }, wb.reports.map((item) => h('option', {
            value: item.id, selected: report && item.id === report.id,
        }, 'версия ' + item.version + ' · ' + (REPORT_STATUS[item.status] || item.status))));

        const generateButton = h('button', {
            class: 'btn' + (report ? '' : ' btn--primary'), disabled: !editable || wb.busy,
            title: report
                ? 'Создать новую версию отчёта целиком (старые версии сохраняются)'
                : 'Пройти по секциям шаблона и написать черновик',
            onclick: () => generateReport(),
        }, report ? 'Перегенерировать всё' : 'Сгенерировать отчёт');

        const verifyButton = h('button', {
            class: 'btn', disabled: !report || wb.busy,
            title: 'Пересчитать замечания верификатора',
            onclick: () => verifyReport(),
        }, 'Проверить');

        const exportButton = h('button', {
            class: 'btn', disabled: !report || wb.busy,
            onclick: () => exportDocx(),
        }, 'Экспорт в DOCX');

        const errors = report ? report.errors || 0 : 0;
        const approveButton = h('button', {
            class: 'btn btn--primary',
            disabled: !report || !editable || errors > 0 || report.status === 'approved' || wb.busy,
            title: errors > 0
                ? 'Утверждение заблокировано: верификатор нашёл ошибок — ' + errors
                : 'Подписать отчёт и сохранить правки в обучающий набор',
            onclick: () => approveReport(),
        }, report && report.status === 'approved' ? 'Утверждён' : 'Утвердить');

        append(head, [
            h('div', { class: 'line' },
                h('div', { class: 'case-title' },
                    wb.case.title || reportTypeTitle(wb.case.report_type),
                    h('span', { class: 'case-id' }, wb.case.case_id)),
                statusBadge(wb.case.status),
                isAdmin() ? h('button', {
                    class: 'btn btn--sm btn--danger',
                    onclick: () => deleteCase(wb.case, () => navigate('#/cases')),
                }, 'Удалить кейс') : null),
            h('div', { class: 'line' },
                wb.reports.length ? versionSelect : h('span', { class: 'small muted' }, 'версий отчёта нет'),
                report ? h('span', { class: 'badge badge--' + (
                    report.status === 'approved' ? 'ok' : report.status === 'verified' ? 'info' : ''
                ) }, REPORT_STATUS[report.status] || report.status) : null,
                report ? h('button', {
                    class: 'counter' + (errors ? ' has-errors' : ''),
                    title: 'Показать замечания',
                    onclick: () => { setTab('issues'); focusSidePanel(); },
                }, '● ошибок: ' + errors) : null,
                report ? h('button', {
                    class: 'counter' + (report.warnings ? ' has-warnings' : ''),
                    title: 'Показать предупреждения',
                    onclick: () => { setTab('issues'); focusSidePanel(); },
                }, '▲ предупреждений: ' + (report.warnings || 0)) : null,
                h('span', { style: { flex: '1' } }),
                generateButton, verifyButton, exportButton, approveButton),
        ]);
    }

    function renderSections() {
        const body = wb.nodes.reportBody;
        clear(body);
        const report = wb.report;

        if (!report) {
            body.appendChild(h('div', { class: 'empty' },
                h('h3', {}, 'Отчёт ещё не сгенерирован'),
                h('div', {}, 'Конвейер пройдёт по секциям шаблона «' + reportTypeTitle(wb.case.report_type) +
                    '», подберёт фрагменты библиотеки и напишет черновик.'),
                h('div', { class: 'btn-row', style: { justifyContent: 'center', marginTop: '14px' } },
                    h('button', {
                        class: 'btn btn--primary', disabled: !canEdit(),
                        onclick: () => generateReport(),
                    }, 'Сгенерировать отчёт'))));
            return;
        }

        const container = h('div', { class: 'sections' });
        report.sections.forEach((section) => container.appendChild(sectionCard(section)));
        if (!report.sections.length) {
            container.appendChild(h('div', { class: 'empty' }, 'В отчёте нет секций.'));
        }
        body.appendChild(container);
        $$('.editor', container).forEach(autosize);
        $$('.section-card', container).forEach(syncBackdrop);
    }

    function sectionCard(section) {
        const editable = canEdit();
        const draft = wb.drafts.has(section.section_id)
            ? wb.drafts.get(section.section_id)
            : section.text;

        const badges = h('div', { class: 'btn-row' });
        const backdrop = h('div', { class: 'editor-backdrop', 'aria-hidden': 'true' });
        const textarea = h('textarea', {
            class: 'editor', spellcheck: 'true', disabled: !editable,
            'aria-label': 'Текст раздела «' + section.title + '»',
        });
        textarea.value = draft;

        const hintInput = h('input', {
            type: 'text', class: 'hint',
            placeholder: 'пожелание к перегенерации: «подробнее про полосу»',
            disabled: !editable,
        });

        const saveButton = h('button', {
            class: 'btn btn--sm btn--primary', disabled: !editable || !wb.dirty.has(section.section_id),
            title: 'Сохранить раздел (Ctrl+S)',
            onclick: () => saveSection(section.section_id),
        }, 'Сохранить');

        const regenButton = h('button', {
            class: 'btn btn--sm', disabled: !editable,
            title: 'Перегенерировать раздел (Ctrl+Enter)',
            onclick: () => regenerateSection(section.section_id),
        }, 'Перегенерировать');

        const restoreButton = h('button', {
            class: 'btn btn--sm', disabled: !editable,
            title: 'Заменить текст раздела черновиком, который написала модель',
            hidden: !section.edited,
            onclick: () => restoreSection(section.section_id),
        }, 'Вернуть черновик модели');

        const card = h('article', {
            class: 'section-card', id: domId('sec-', section.section_id),
            dataset: { section: section.section_id },
        },
            h('header', {},
                h('h3', {}, h('span', { class: 'ord' }, (section.ord + 1) + '.'), ' ', section.title),
                badges,
                h('button', {
                    class: 'btn btn--icon btn--ghost', title: 'Свернуть или развернуть раздел',
                    onclick: (event) => {
                        const collapsed = card.classList.toggle('is-collapsed');
                        $$('.editor-wrap, .section-actions, .section-sources', card)
                            .forEach((node) => { node.hidden = collapsed; });
                        event.currentTarget.textContent = collapsed ? '▸' : '▾';
                    },
                }, '▾')),
            h('div', { class: 'editor-wrap' }, backdrop, textarea),
            h('div', { class: 'section-actions' }, hintInput, regenButton, saveButton, restoreButton),
            sourceChips(section));

        textarea.addEventListener('input', () => {
            wb.drafts.set(section.section_id, textarea.value);
            if (textarea.value !== section.text) wb.dirty.add(section.section_id);
            else wb.dirty.delete(section.section_id);
            saveButton.disabled = !wb.dirty.has(section.section_id);
            autosize(textarea);
            syncBackdrop(card);
            renderBadges(badges, section);
        });
        textarea.addEventListener('focus', () => { wb.focused = section.section_id; });

        renderBadges(badges, section);
        return card;
    }

    function renderBadges(container, section) {
        clear(container);
        const items = [];
        if (wb.dirty.has(section.section_id)) items.push(h('span', { class: 'badge badge--accent' }, 'не сохранено'));
        if (section.edited) items.push(h('span', { class: 'badge badge--warn' }, 'правлено'));
        if (section.regenerated > 0) {
            items.push(h('span', { class: 'badge badge--info' },
                'перегенерировано ' + section.regenerated + ' ' +
                plural(section.regenerated, 'раз', 'раза', 'раз')));
        }
        if (section.missing_facts && section.missing_facts.length) {
            items.push(h('span', {
                class: 'badge badge--danger',
                title: 'Отсутствующие ключи: ' + section.missing_facts.join(', '),
            }, 'не хватает данных: ' + section.missing_facts.join(', ')));
        }
        append(container, items);
    }

    function sourceChips(section) {
        const labels = section.sources || [];
        if (!labels.length) {
            return h('div', { class: 'section-sources' }, 'источники не привлекались');
        }
        return h('div', { class: 'section-sources' }, 'источники раздела:',
            labels.map((label) => h('button', {
                class: 'chip chip--src' + (wb.activeSource === label ? ' is-active' : ''),
                dataset: { label: label },
                title: 'Подсветить ссылки [' + label + '] в тексте',
                onclick: () => setActiveSource(label),
            }, label)));
    }

    function autosize(textarea) {
        textarea.style.height = 'auto';
        textarea.style.height = (textarea.scrollHeight + 2) + 'px';
    }

    function syncBackdrop(card) {
        const textarea = $('.editor', card);
        const backdrop = $('.editor-backdrop', card);
        if (!textarea || !backdrop) return;
        const text = textarea.value + '\n';
        backdrop.innerHTML = escapeHtml(text).replace(/\[(S\d+)\]/g, (match, label) =>
            '<mark class="src-mark' + (wb.activeSource === label ? ' is-active' : '') + '">' + match + '</mark>');
    }

    function setActiveSource(label) {
        wb.activeSource = wb.activeSource === label ? null : label;
        $$('.section-card').forEach(syncBackdrop);
        $$('.chip--src').forEach((chip) => {
            chip.classList.toggle('is-active', chip.dataset.label === wb.activeSource);
        });
        renderSidePanel();
        if (!wb.activeSource) return;
        const section = (wb.report ? wb.report.sections : []).find((item) => {
            const text = wb.drafts.has(item.section_id) ? wb.drafts.get(item.section_id) : item.text;
            return String(text).indexOf('[' + wb.activeSource + ']') !== -1;
        });
        if (section) flashSection(section.section_id);
    }

    function flashSection(sectionId) {
        const card = document.getElementById(domId('sec-', sectionId));
        if (!card) return;
        const workbench = document.getElementById('workbench');
        if (workbench && window.innerWidth < 1280) workbench.dataset.panel = 'report';
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        card.classList.remove('is-flash');
        void card.offsetWidth;
        card.classList.add('is-flash');
        setTimeout(() => card.classList.remove('is-flash'), 1600);
    }

    function focusSidePanel() {
        const workbench = document.getElementById('workbench');
        if (workbench && window.innerWidth < 1280) workbench.dataset.panel = 'side';
    }

    function setSectionBusy(sectionId, busy, note) {
        const card = document.getElementById(domId('sec-', sectionId));
        if (!card) return;
        card.classList.toggle('is-busy', busy);
        const existing = $('.progress', card);
        if (busy && !existing) {
            const bar = h('div', { class: 'progress', title: note || '' }, h('i', {}));
            card.insertBefore(bar, card.firstChild.nextSibling);
        } else if (!busy && existing) {
            existing.remove();
        }
    }

    function lockLongOperation() {
        if (wb.busy) {
            toast('Дождитесь окончания текущей операции', 'error', 4000);
            return false;
        }
        wb.busy = true;
        renderReportHead();
        return true;
    }

    function unlockLongOperation() {
        wb.busy = false;
        renderReportHead();
    }

    async function generateReport() {
        if (!canEdit()) {
            toast('Недостаточно прав: генерация доступна инженеру', 'error');
            return;
        }
        if (wb.dirty.size) {
            const ok = await confirmDialog({
                title: 'Есть несохранённые правки',
                message: 'Будет создана новая версия отчёта. Несохранённые правки в текущей версии пропадут.',
                confirmText: 'Всё равно сгенерировать',
                danger: true,
            });
            if (!ok) return;
        }
        if (wb.factsDirty) {
            const ok = await confirmDialog({
                title: 'Факт-пакет не сохранён',
                message: 'Модель получит последний сохранённый факт-пакет, а не текущие правки. Продолжить?',
                confirmText: 'Генерировать',
            });
            if (!ok) return;
        }
        if (!lockLongOperation()) return;
        try {
            const data = await withOverlay(
                'Идёт генерация отчёта',
                'Секции пишутся последовательно, каждая со своими фактами и источниками. ' +
                'На отчёт в десятки страниц уходят минуты — не закрывайте вкладку.',
                () => api.post('/api/cases/' + wb.case.id + '/generate', {}));
            wb.drafts.clear();
            wb.dirty.clear();
            await reloadCase(data.report ? data.report.id : null);
            toast('Отчёт сгенерирован: версия ' + (wb.report ? wb.report.version : '?'), 'ok');
        } catch (error) {
            toastError(error);
        } finally {
            unlockLongOperation();
        }
    }

    async function reloadCase(preferReportId) {
        const data = await api.get('/api/cases/' + wb.case.id);
        wb.case = data.case;
        wb.coverage = data.coverage || {};
        wb.reports = (data.reports || []).slice().sort((a, b) => a.version - b.version);
        if (!wb.factsDirty) {
            wb.facts = clone(wb.case.facts || {});
            rebuildFactRows();
        }
        const target = preferReportId ||
            (wb.reports.length ? wb.reports[wb.reports.length - 1].id : null);
        if (target) {
            wb.report = await loadReport(target);
        } else {
            wb.report = null;
        }
        refreshAll();
    }

    async function switchVersion(reportId) {
        if (!reportId || (wb.report && wb.report.id === reportId)) return;
        if (wb.dirty.size) {
            const ok = await confirmDialog({
                title: 'Есть несохранённые правки',
                message: 'Переключение версии отчёта потеряет несохранённые правки (' +
                    wb.dirty.size + ' ' + plural(wb.dirty.size, 'раздел', 'раздела', 'разделов') + ').',
                confirmText: 'Переключить',
                danger: true,
            });
            if (!ok) {
                renderReportHead();
                return;
            }
        }
        wb.drafts.clear();
        wb.dirty.clear();
        try {
            wb.report = await loadReport(reportId);
            refreshAll();
        } catch (error) {
            toastError(error);
        }
    }

    function mergeReportPatch(patch) {
        if (!patch || !wb.report) return;
        ['status', 'errors', 'warnings', 'issues', 'markdown', 'version', 'meta', 'approved_at']
            .forEach((key) => {
                if (patch[key] !== undefined) wb.report[key] = patch[key];
            });
        if (Array.isArray(patch.sources)) wb.report.sources = patch.sources;
        if (Array.isArray(patch.sections) && patch.sections.length) {
            wb.report.sections = patch.sections.slice().sort((a, b) => (a.ord || 0) - (b.ord || 0));
        }
        if (patch.issues !== undefined) {
            if (patch.errors === undefined) {
                wb.report.errors = wb.report.issues.filter((issue) => issue.level === 'error').length;
            }
            if (patch.warnings === undefined) {
                wb.report.warnings = wb.report.issues.filter((issue) => issue.level === 'warning').length;
            }
        }
    }

    function replaceSectionCard(sectionId) {
        const section = (wb.report.sections || []).find((item) => item.section_id === sectionId);
        const old = document.getElementById(domId('sec-', sectionId));
        if (!section || !old) {
            renderSections();
            return;
        }
        const card = sectionCard(section);
        old.replaceWith(card);
        autosize($('.editor', card));
        syncBackdrop(card);
    }

    async function saveSection(sectionId) {
        if (!wb.report || !canEdit()) return;
        if (!wb.dirty.has(sectionId)) {
            toast('В разделе нет несохранённых правок', 'info', 3000);
            return;
        }
        const text = wb.drafts.get(sectionId);
        setSectionBusy(sectionId, true, 'Сохранение и пересборка отчёта');
        try {
            const data = await api.put(
                '/api/reports/' + wb.report.id + '/sections/' + encodeURIComponent(sectionId),
                { text: text });
            applySectionResult(sectionId, data);
            toast('Раздел сохранён, отчёт пересобран и перепроверен', 'ok', 4000);
        } catch (error) {
            toastError(error);
        } finally {
            setSectionBusy(sectionId, false);
        }
    }

    async function regenerateSection(sectionId) {
        if (!wb.report || !canEdit()) return;
        if (wb.dirty.has(sectionId)) {
            const ok = await confirmDialog({
                title: 'Перегенерировать раздел',
                message: 'Несохранённые правки этого раздела будут заменены новым текстом модели.',
                confirmText: 'Перегенерировать',
                danger: true,
            });
            if (!ok) return;
        }
        if (!lockLongOperation()) return;
        const card = document.getElementById(domId('sec-', sectionId));
        const hintInput = card ? $('.hint', card) : null;
        const hint = hintInput ? hintInput.value.trim() : '';
        setSectionBusy(sectionId, true, 'Модель пишет раздел заново');
        try {
            const data = await api.post(
                '/api/reports/' + wb.report.id + '/sections/' + encodeURIComponent(sectionId) + '/regenerate',
                hint ? { hint: hint } : {});
            applySectionResult(sectionId, data);
            toast('Раздел перегенерирован', 'ok', 4000);
        } catch (error) {
            toastError(error);
        } finally {
            setSectionBusy(sectionId, false);
            unlockLongOperation();
        }
    }

    async function restoreSection(sectionId) {
        if (!wb.report || !canEdit()) return;
        const section = wb.report.sections.find((item) => item.section_id === sectionId);
        if (!section) return;
        const ok = await confirmDialog({
            title: 'Вернуть черновик модели',
            message: 'Текущий текст раздела «' + section.title +
                '» будет заменён исходным черновиком модели.',
            confirmText: 'Вернуть черновик',
            danger: true,
        });
        if (!ok) return;

        setSectionBusy(sectionId, true, 'Возврат черновика');
        const base = '/api/reports/' + wb.report.id + '/sections/' + encodeURIComponent(sectionId);
        try {
            let data;
            try {
                data = await api.post(base + '/restore', {});
            } catch (error) {
                // Если отдельного маршрута возврата нет, пишем черновик как обычную правку.
                if (error instanceof ApiError && (error.status === 404 || error.status === 405)) {
                    data = await api.put(base, { text: section.draft_text || '' });
                } else {
                    throw error;
                }
            }
            applySectionResult(sectionId, data);
            toast('Черновик модели возвращён', 'ok', 4000);
        } catch (error) {
            toastError(error);
        } finally {
            setSectionBusy(sectionId, false);
        }
    }

    function applySectionResult(sectionId, data) {
        wb.drafts.delete(sectionId);
        wb.dirty.delete(sectionId);
        mergeReportPatch(data.report);
        if (data.section) {
            const index = wb.report.sections.findIndex((item) => item.section_id === sectionId);
            if (index === -1) wb.report.sections.push(data.section);
            else wb.report.sections[index] = data.section;
            wb.report.sections.sort((a, b) => (a.ord || 0) - (b.ord || 0));
        }
        replaceSectionCard(sectionId);
        renderReportHead();
        renderSidePanel();
    }

    async function verifyReport() {
        if (!wb.report) return;
        try {
            const data = await withOverlay('Проверка отчёта', 'Верификатор сверяет числа, ссылки и структуру.',
                () => api.post('/api/reports/' + wb.report.id + '/verify', {}));
            wb.report.issues = data.issues || [];
            wb.report.errors = data.errors !== undefined
                ? data.errors
                : wb.report.issues.filter((issue) => issue.level === 'error').length;
            wb.report.warnings = data.warnings !== undefined
                ? data.warnings
                : wb.report.issues.filter((issue) => issue.level === 'warning').length;
            renderReportHead();
            setTab('issues');
            const errors = wb.report.errors;
            toast(errors
                ? 'Проверка завершена: ошибок — ' + errors + ', предупреждений — ' + wb.report.warnings
                : 'Ошибок нет, предупреждений — ' + wb.report.warnings,
                errors ? 'error' : 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    async function approveReport() {
        if (!wb.report) return;
        if ((wb.report.errors || 0) > 0) {
            toast('Утверждение заблокировано: сначала устраните ошибки верификатора', 'error');
            return;
        }
        if (wb.dirty.size) {
            toast('Сначала сохраните правки разделов: ' + wb.dirty.size, 'error');
            return;
        }
        const ok = await confirmDialog({
            title: 'Утвердить отчёт',
            message: 'Отчёт версии ' + wb.report.version + ' по кейсу ' + wb.case.case_id +
                ' будет подписан. Кейс перейдёт в статус «утверждён».',
            note: 'Пары «черновик модели → финал инженера» по изменённым разделам уйдут в обучающий набор.',
            confirmText: 'Утвердить',
        });
        if (!ok) return;
        try {
            const data = await withOverlay('Утверждение отчёта', 'Сохраняются правки для обучающего набора.',
                () => api.post('/api/reports/' + wb.report.id + '/approve', {}));
            wb.report = normalizeReport(data.report) || wb.report;
            await reloadCase(wb.report.id);
            toast('Отчёт утверждён', 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    async function exportDocx() {
        if (!wb.report) return;
        try {
            const result = await withOverlay('Сборка DOCX', 'Отчёт собирается по фирменному шаблону.',
                () => api.download('/api/reports/' + wb.report.id + '/export.docx'));
            const url = URL.createObjectURL(result.blob);
            const link = h('a', { href: url, download: result.filename });
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 30000);
            toast('Файл ' + result.filename + ' выгружен', 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    // -- правая панель ------------------------------------------------------

    function buildSidePanel() {
        const tabs = h('div', { class: 'tabs' });
        const body = h('div', { class: 'panel-body' });
        wb.nodes.sideTabs = tabs;
        wb.nodes.sideBody = body;
        return h('section', { class: 'panel panel--side' }, tabs, body);
    }

    function setTab(tab) {
        wb.tab = tab;
        renderSidePanel();
    }

    function renderSidePanel() {
        const tabs = wb.nodes.sideTabs;
        const body = wb.nodes.sideBody;
        if (!tabs || !body) return;
        const report = wb.report;
        const sources = report ? report.sources || [] : [];
        const issues = report ? report.issues || [] : [];

        clear(tabs);
        append(tabs, [
            h('button', {
                class: wb.tab === 'sources' ? 'is-active' : '',
                onclick: () => setTab('sources'),
            }, 'Источники (' + sources.length + ')'),
            h('button', {
                class: wb.tab === 'issues' ? 'is-active' : '',
                onclick: () => setTab('issues'),
            }, 'Замечания (' + issues.length + ')'),
        ]);

        clear(body);
        if (wb.tab === 'sources') renderSources(body, sources);
        else renderIssues(body, issues);
    }

    function renderSources(body, sources) {
        if (!sources.length) {
            body.appendChild(h('div', { class: 'empty' },
                h('h3', {}, 'Источников нет'),
                h('div', {}, wb.report
                    ? 'Модель не привлекала фрагменты библиотеки: индекс пуст или разделы их не требуют.'
                    : 'Появятся после генерации отчёта.')));
            return;
        }
        body.appendChild(h('div', { class: 'small muted', style: { marginBottom: '8px' } },
            'Клик по источнику подсвечивает ссылки [S…] в тексте разделов.'));
        sources.forEach((source) => {
            const active = wb.activeSource === source.label;
            body.appendChild(h('div', {
                class: 'source-item' + (active ? ' is-active is-open' : ''),
                onclick: () => setActiveSource(source.label),
            },
                h('div', {},
                    h('span', { class: 'label' }, '[' + source.label + ']'),
                    h('span', { class: 'citation' }, source.citation || source.chunk_uid || '')),
                h('div', { class: 'quote' }, source.text || '')));
        });
    }

    function renderIssues(body, issues) {
        if (!issues.length) {
            body.appendChild(h('div', { class: 'empty' },
                h('h3', {}, 'Замечаний нет'),
                h('div', {}, wb.report
                    ? 'Верификатор не нашёл ни чисел мимо факт-пакета, ни ссылок в никуда.'
                    : 'Появятся после генерации и проверки.')));
            return;
        }
        ['error', 'warning', 'info'].forEach((level) => {
            const group = issues.filter((issue) => issue.level === level);
            if (!group.length) return;
            body.appendChild(h('div', { class: 'group-title' }, LEVEL_LABEL[level] + ' (' + group.length + ')'));
            group.forEach((issue) => {
                body.appendChild(h('div', {
                    class: 'issue-item level-' + level,
                    title: 'Перейти к разделу',
                    onclick: () => jumpToIssue(issue),
                },
                    h('span', { class: 'dot' }),
                    h('div', { class: 'body' },
                        h('div', { class: 'code' }, issue.code || ''),
                        h('div', { class: 'message' }, issue.message || ''),
                        issue.section
                            ? h('div', { class: 'where' }, 'раздел: ' + issue.section)
                            : h('div', { class: 'where' }, 'относится к отчёту целиком'))));
            });
        });
    }

    function jumpToIssue(issue) {
        if (!issue.section || !wb.report) {
            toast('Замечание относится к отчёту целиком', 'info', 3500);
            return;
        }
        const wanted = normalizeTitle(issue.section);
        const section = wb.report.sections.find((item) => normalizeTitle(item.title) === wanted) ||
            wb.report.sections.find((item) => normalizeTitle(item.title).indexOf(wanted) !== -1) ||
            wb.report.sections.find((item) => item.section_id === issue.section);
        if (!section) {
            toast('Раздел «' + issue.section + '» не найден среди секций текущей версии', 'info');
            return;
        }
        flashSection(section.section_id);
    }

    function refreshAll() {
        renderFactsBody();
        renderReportHead();
        renderSections();
        renderSidePanel();
    }

    // -- горячие клавиши ----------------------------------------------------

    function onKeyDown(event) {
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        if (state.route.name !== 'case') return;
        const target = event.target;
        const card = target && target.closest ? target.closest('.section-card') : null;

        if (event.code === 'KeyS') {
            event.preventDefault();
            if (card) saveSection(card.dataset.section);
            else if (target && target.closest && target.closest('.panel--facts')) saveFacts();
            else if (wb.focused && wb.dirty.has(wb.focused)) saveSection(wb.focused);
            else if (wb.factsDirty) saveFacts();
            else toast('Нечего сохранять', 'info', 2500);
            return;
        }
        if (event.code === 'Enter' || event.code === 'NumpadEnter') {
            if (!card) return;
            event.preventDefault();
            regenerateSection(card.dataset.section);
        }
    }

    // =====================================================================
    // 7. Экран «Библиотека»
    // =====================================================================

    const libState = { docType: '', items: [], stats: {}, chunks: 0, embeddings: 0 };

    async function renderLibrary(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        const tableBox = h('div', { class: 'card' });
        const statsLine = h('div', { class: 'small muted' });
        const uploadList = h('div', {});
        const searchResults = h('div', {});

        const typeFilter = h('select', {
            onchange: (event) => {
                libState.docType = event.target.value;
                loadLibrary();
            },
        }, h('option', { value: '' }, 'Все типы'),
            (state.config.doc_types || []).map((type) =>
                h('option', { value: type, selected: libState.docType === type }, docTypeLabel(type))));

        const forceCheckbox = h('input', { type: 'checkbox' });
        const uploadType = h('select', {}, (state.config.doc_types || []).map((type) =>
            h('option', { value: type }, docTypeLabel(type))));
        const uploadConf = h('select', {}, (state.config.confidentiality || ['public', 'internal', 'nda'])
            .map((value) => h('option', {
                value: value, selected: value === 'internal',
            }, CONFIDENTIALITY_LABEL[value] || value)));

        const fileInput = h('input', {
            type: 'file', multiple: true, style: { display: 'none' },
            accept: '.pdf,.docx,.md,.markdown,.txt',
            onchange: (event) => {
                handleFiles(Array.prototype.slice.call(event.target.files || []));
                event.target.value = '';
            },
        });

        const dropzone = h('div', {
            class: 'dropzone',
            onclick: () => fileInput.click(),
            ondragover: (event) => {
                event.preventDefault();
                dropzone.classList.add('is-over');
            },
            ondragleave: () => dropzone.classList.remove('is-over'),
            ondrop: (event) => {
                event.preventDefault();
                dropzone.classList.remove('is-over');
                handleFiles(Array.prototype.slice.call(event.dataTransfer.files || []));
            },
        },
            h('div', {}, h('b', {}, 'Перетащите файлы сюда'), ' или нажмите для выбора'),
            h('div', { class: 'small' }, 'PDF, DOCX, Markdown, TXT. Конвертация, нарезка и индексация — при загрузке.'));

        const searchInput = h('input', {
            type: 'search', class: 'grow', placeholder: 'Проверка поиска: запрос по библиотеке',
            onkeydown: (event) => {
                if (event.key === 'Enter') runSearch();
            },
        });
        const topKInput = h('input', { type: 'number', value: '10', min: '1', max: '50', style: { width: '70px' } });
        const searchTypes = (state.config.doc_types || []).map((type) => {
            const checkbox = h('input', { type: 'checkbox', value: type });
            return { type: type, checkbox: checkbox, node: h('label', { class: 'inline' }, checkbox, docTypeLabel(type)) };
        });

        append(page, [
            h('div', { class: 'page-head' },
                h('h1', {}, 'Библиотека'),
                statsLine,
                h('button', { class: 'btn', onclick: () => loadLibrary() }, 'Обновить'),
                canEdit() ? h('label', { class: 'inline' }, forceCheckbox, 'принудительно') : null,
                canEdit() ? h('button', { class: 'btn', onclick: () => reindex() }, 'Переиндексировать') : null),

            canEdit() ? h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Загрузка документов'),
                h('div', { class: 'toolbar' },
                    h('label', { class: 'inline' }, 'Тип:', uploadType),
                    h('label', { class: 'inline' }, 'Гриф:', uploadConf)),
                dropzone, fileInput, uploadList) : null,

            h('div', { class: 'toolbar', style: { marginTop: '14px' } }, typeFilter),
            tableBox,

            h('div', { class: 'card card-pad', style: { marginTop: '14px' } },
                h('div', { class: 'card-title' }, 'Проверка поиска'),
                h('div', { class: 'toolbar' },
                    searchInput, topKInput,
                    h('button', { class: 'btn btn--primary', onclick: () => runSearch() }, 'Найти')),
                h('div', { class: 'toolbar small muted' }, 'типы:', searchTypes.map((item) => item.node)),
                searchResults),
        ]);

        async function loadLibrary() {
            clear(tableBox);
            tableBox.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка…'));
            try {
                const data = await api.get('/api/library' +
                    (libState.docType ? '?doc_type=' + encodeURIComponent(libState.docType) : ''));
                libState.items = data.items || [];
                libState.stats = data.stats || {};
                libState.chunks = data.chunks || 0;
                libState.embeddings = data.embeddings || 0;
                renderTable();
            } catch (error) {
                clear(tableBox);
                tableBox.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        function renderTable() {
            const totals = Object.keys(libState.stats).reduce((acc, type) => {
                acc.documents += libState.stats[type].documents || 0;
                acc.chunks += libState.stats[type].chunks || 0;
                return acc;
            }, { documents: 0, chunks: 0 });
            statsLine.textContent = 'документов: ' + totals.documents + ' · чанков: ' +
                (libState.chunks || totals.chunks) +
                (libState.embeddings ? ' · векторов: ' + libState.embeddings : ' · векторов нет (плотный поиск выключен)');

            clear(tableBox);
            if (!libState.items.length) {
                tableBox.appendChild(h('div', { class: 'empty' },
                    h('h3', {}, 'Документов нет'),
                    h('div', {}, 'Загрузите литературу, стандарты и прошлые отчёты — они станут источниками для ссылок.')));
                return;
            }
            const body = h('tbody', {});
            libState.items.forEach((item) => {
                body.appendChild(h('tr', {},
                    h('td', {}, item.title || item.doc_id),
                    h('td', { class: 'mono small muted' }, item.doc_id),
                    h('td', { class: 'small' }, docTypeLabel(item.doc_type)),
                    h('td', { class: 'num' }, item.chunk_count || 0),
                    h('td', { class: 'small muted nowrap' }, fmtDateTime(item.indexed_at)),
                    h('td', { class: 'small' }, CONFIDENTIALITY_LABEL[item.confidentiality] || item.confidentiality),
                    h('td', {}, isAdmin() ? h('button', {
                        class: 'btn btn--icon btn--danger', title: 'Удалить документ',
                        onclick: () => removeDocument(item),
                    }, '×') : null)));
            });
            tableBox.appendChild(h('div', { class: 'table-scroll' },
                h('table', { class: 'grid' },
                    h('thead', {}, h('tr', {},
                        h('th', {}, 'Название'), h('th', {}, 'Идентификатор'), h('th', {}, 'Тип'),
                        h('th', {}, 'Чанков'), h('th', {}, 'Проиндексирован'), h('th', {}, 'Гриф'), h('th', {}))),
                    body)));
        }

        async function handleFiles(files) {
            if (!files.length) return;
            for (const file of files) {
                const bar = h('i', {});
                const status = h('span', { class: 'small muted' }, 'загрузка…');
                const row = h('div', { class: 'upload-row' },
                    h('span', { class: 'small' }, file.name),
                    h('span', { class: 'small faint' }, fmtBytes(file.size)),
                    h('span', { class: 'bar' }, bar), status);
                uploadList.appendChild(row);
                const form = new FormData();
                form.append('file', file);
                form.append('doc_type', uploadType.value);
                form.append('confidentiality', uploadConf.value);
                try {
                    const data = await uploadFile('/api/library/upload', form, (fraction) => {
                        bar.style.width = Math.round(fraction * 100) + '%';
                    });
                    bar.style.width = '100%';
                    const result = data.result || {};
                    const chunks = result.chunks !== undefined && result.chunks !== null ? result.chunks : '—';
                    status.textContent = 'готово, чанков: ' + chunks;
                    status.className = 'small';
                    if (result.failed) {
                        status.textContent = 'файл сохранён, но не проиндексирован';
                    }
                } catch (error) {
                    status.textContent = errorText(error);
                    status.className = 'small';
                    status.style.color = 'var(--danger)';
                }
            }
            await loadLibrary();
        }

        async function reindex() {
            const force = forceCheckbox.checked;
            if (force) {
                const ok = await confirmDialog({
                    title: 'Принудительная переиндексация',
                    message: 'Все документы библиотеки будут разобраны и нарезаны заново, даже если файл не менялся.',
                    confirmText: 'Переиндексировать',
                });
                if (!ok) return;
            }
            try {
                const data = await withOverlay('Переиндексация библиотеки',
                    'Файлы с неизменившимся SHA-256 пропускаются.',
                    () => api.post('/api/library/reindex', { force: force }));
                const result = data.result || {};
                toast('Переиндексация завершена: добавлено ' + (result.added || 0) +
                    ', обновлено ' + (result.updated || 0) +
                    ', пропущено ' + (result.skipped || 0) +
                    ', ошибок ' + (result.failed || 0) +
                    ', чанков ' + (result.chunks || 0), (result.failed ? 'error' : 'ok'), 9000);
                await loadLibrary();
            } catch (error) {
                toastError(error);
            }
        }

        async function removeDocument(item) {
            const ok = await confirmDialog({
                title: 'Удалить документ',
                message: 'Документ «' + (item.title || item.doc_id) +
                    '» и все его чанки будут удалены из индекса.',
                note: 'Уже сгенерированные отчёты сохраняют цитаты в своём приложении и не пострадают.',
                confirmText: 'Удалить',
                danger: true,
            });
            if (!ok) return;
            try {
                await api.del('/api/library/' + encodePath(item.doc_id));
                toast('Документ удалён', 'ok');
                await loadLibrary();
            } catch (error) {
                toastError(error);
            }
        }

        async function runSearch() {
            const query = searchInput.value.trim();
            if (!query) {
                toast('Введите поисковый запрос', 'error', 3000);
                return;
            }
            const types = searchTypes.filter((item) => item.checkbox.checked).map((item) => item.type);
            clear(searchResults);
            searchResults.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Поиск…'));
            try {
                const url = '/api/search?q=' + encodeURIComponent(query) +
                    '&top_k=' + encodeURIComponent(topKInput.value || '10') +
                    (types.length ? '&doc_types=' + encodeURIComponent(types.join(',')) : '');
                const data = await api.get(url);
                clear(searchResults);
                if (data.note) searchResults.appendChild(h('div', { class: 'small muted' }, data.note));
                const items = data.items || [];
                if (!items.length) {
                    searchResults.appendChild(h('div', { class: 'empty' }, 'Ничего не найдено.'));
                    return;
                }
                items.forEach((hit) => {
                    searchResults.appendChild(h('div', { class: 'search-hit' },
                        h('div', { class: 'head' },
                            h('b', {}, hit.citation || hit.chunk_uid),
                            h('span', { class: 'badge' }, docTypeLabel(hit.doc_type)),
                            h('span', { class: 'faint small' }, 'вес ' + fmtNumber(hit.score, 3))),
                        h('div', { class: 'text' }, hit.text || '')));
                });
            } catch (error) {
                clear(searchResults);
                searchResults.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        await loadLibrary();
    }

    // =====================================================================
    // 8. Экран «Метрики» и журнал действий
    // =====================================================================

    async function renderStats(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        const data = await api.get('/api/stats');
        const cases = data.cases || {};
        const reports = data.reports || {};
        const edits = data.edits || {};
        const library = data.library || {};

        const cards = h('div', { class: 'stat-cards' },
            statCard(cases.total || 0, 'кейсов всего',
                'утверждено: ' + (cases.approved || 0) + ' · черновиков: ' + (cases.draft || 0)),
            statCard(reports.total || 0, 'версий отчётов',
                'утверждено: ' + (reports.approved || 0)),
            statCard(fmtNumber(edits.mean_distance || 0, 3), 'средний edit distance',
                'правок в наборе: ' + (edits.count || 0) + ' · чем меньше, тем ближе черновик к финалу'),
            statCard(library.documents || 0, 'документов в библиотеке',
                'чанков: ' + (library.chunks || 0) +
                (library.embeddings ? ' · векторов: ' + library.embeddings : ' · векторов нет')));

        const bySection = (edits.by_section || []).slice()
            .sort((a, b) => (b.pairs || 0) - (a.pairs || 0));
        const maxPairs = bySection.reduce((max, item) => Math.max(max, item.pairs || 0), 0) || 1;

        const editsCard = h('div', { class: 'card card-pad' },
            h('div', { class: 'card-title' }, 'Какие разделы правят чаще всего'),
            h('div', { class: 'small muted', style: { marginBottom: '10px' } },
                'Главный график для руководства (док. 05): где модель систематически не попадает — ' +
                'там надо править инструкцию раздела в шаблоне, а не модель.'));

        if (!bySection.length) {
            editsCard.appendChild(h('div', { class: 'empty' },
                'Пар «черновик → финал» ещё нет: они появляются при утверждении отчётов с правками.'));
        } else {
            const body = h('tbody', {});
            bySection.forEach((item) => {
                const share = (item.pairs || 0) / maxPairs;
                const distance = Number(item.mean_distance || 0);
                body.appendChild(h('tr', {},
                    h('td', {}, item.section_title || item.section_id),
                    h('td', { class: 'mono small muted' }, item.section_id),
                    h('td', {}, h('div', { class: 'bar-cell' },
                        h('span', { class: 'track' }, h('i', { style: { width: Math.round(share * 100) + '%' } })),
                        h('span', { class: 'num' }, String(item.pairs || 0)))),
                    h('td', {}, h('div', { class: 'bar-cell' },
                        h('span', { class: 'track' }, h('i', {
                            class: distance > 0.5 ? 'hot' : '',
                            style: { width: Math.round(Math.min(1, distance) * 100) + '%' },
                        })),
                        h('span', { class: 'num' }, fmtNumber(distance, 3))))));
            });
            editsCard.appendChild(h('div', { class: 'table-scroll' },
                h('table', { class: 'grid' },
                    h('thead', {}, h('tr', {},
                        h('th', {}, 'Раздел'), h('th', {}, 'Идентификатор'),
                        h('th', {}, 'Правок'), h('th', {}, 'Средняя дистанция правки'))),
                    body)));
        }

        const byType = library.by_type || {};
        const libCard = h('div', { class: 'card card-pad' },
            h('div', { class: 'card-title' }, 'Состав библиотеки'));
        const typeKeys = Object.keys(byType);
        if (!typeKeys.length) {
            libCard.appendChild(h('div', { class: 'empty' }, 'Библиотека пуста.'));
        } else {
            const body = h('tbody', {});
            typeKeys.forEach((type) => {
                body.appendChild(h('tr', {},
                    h('td', {}, docTypeLabel(type)),
                    h('td', { class: 'num' }, byType[type].documents || 0),
                    h('td', { class: 'num' }, byType[type].chunks || 0)));
            });
            libCard.appendChild(h('div', { class: 'table-scroll' },
                h('table', { class: 'grid' },
                    h('thead', {}, h('tr', {},
                        h('th', {}, 'Тип'), h('th', {}, 'Документов'), h('th', {}, 'Чанков'))),
                    body)));
        }

        append(page, [
            h('div', { class: 'page-head' }, h('h1', {}, 'Метрики'),
                h('button', { class: 'btn', onclick: () => renderRoute(state.route) }, 'Обновить')),
            cards, editsCard, libCard,
        ]);

        if (isAdmin()) page.appendChild(await auditCard());
    }

    function statCard(value, label, sub) {
        return h('div', { class: 'stat' },
            h('div', { class: 'value' }, String(value)),
            h('div', { class: 'label' }, label),
            sub ? h('div', { class: 'sub' }, sub) : null);
    }

    async function auditCard() {
        const box = h('div', { class: 'card card-pad', style: { marginTop: '14px' } });
        const content = h('div', {});
        const limitSelect = h('select', {
            onchange: () => load(),
        }, ['50', '200', '500'].map((value) =>
            h('option', { value: value, selected: value === '200' }, 'последние ' + value)));

        append(box, [
            h('div', { class: 'card-title' }, 'Журнал действий',
                h('span', { style: { flex: '1' } }),
                limitSelect,
                h('button', { class: 'btn btn--sm', onclick: () => load() }, 'Обновить')),
            content,
        ]);

        async function load() {
            clear(content);
            content.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка журнала…'));
            try {
                const data = await api.get('/api/audit?limit=' + encodeURIComponent(limitSelect.value));
                const items = data.items || [];
                clear(content);
                if (!items.length) {
                    content.appendChild(h('div', { class: 'empty' }, 'Записей нет.'));
                    return;
                }
                const body = h('tbody', {});
                items.forEach((entry) => {
                    const details = entry.details && Object.keys(entry.details).length
                        ? JSON.stringify(entry.details) : '';
                    body.appendChild(h('tr', {},
                        h('td', { class: 'small muted nowrap' }, fmtDateTime(entry.ts)),
                        h('td', { class: 'small' }, entry.login || '—'),
                        h('td', { class: 'small' }, AUDIT_LABEL[entry.action] || entry.action),
                        h('td', { class: 'mono small muted' },
                            (entry.object_type ? entry.object_type + ' ' : '') + (entry.object_id || '')),
                        h('td', { class: 'mono small faint' }, details)));
                });
                content.appendChild(h('div', { class: 'table-scroll' },
                    h('table', { class: 'grid' },
                        h('thead', {}, h('tr', {},
                            h('th', {}, 'Время'), h('th', {}, 'Пользователь'), h('th', {}, 'Действие'),
                            h('th', {}, 'Объект'), h('th', {}, 'Подробности'))),
                        body)));
            } catch (error) {
                clear(content);
                content.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        await load();
        return box;
    }

    // =====================================================================
    // 9. Запуск
    // =====================================================================

    async function start() {
        applyTheme(storageGet('rg-theme', 'auto'));

        let me;
        try {
            me = await api.get('/api/me');
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) return;
            fatal(errorText(error));
            return;
        }
        state.user = me.user;
        state.authEnabled = me.auth_enabled !== false;
        if (state.authEnabled && !state.user) {
            goToLogin();
            return;
        }

        try {
            state.config = await api.get('/api/config');
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) return;
            fatal(errorText(error));
            return;
        }

        renderChrome();

        window.addEventListener('hashchange', onHashChange);
        window.addEventListener('beforeunload', (event) => {
            if (!hasUnsaved()) return;
            event.preventDefault();
            event.returnValue = '';
        });
        window.addEventListener('resize', debounce(() => {
            $$('.editor').forEach(autosize);
            $$('.section-card').forEach(syncBackdrop);
        }, 150));
        document.addEventListener('keydown', onKeyDown);

        if (!location.hash) location.hash = '#/cases';
        currentHash = location.hash;
        await renderRoute(parseHash(currentHash));
    }

    function fatal(message) {
        const view = $('#view');
        clear(view);
        view.appendChild(h('div', { class: 'page' },
            h('div', { class: 'card card-pad' },
                h('h3', { style: { color: 'var(--danger)', marginBottom: '6px' } },
                    'Интерфейс не смог связаться с сервером'),
                h('div', { class: 'muted' }, message),
                h('div', { class: 'btn-row', style: { marginTop: '12px' } },
                    h('button', { class: 'btn btn--primary', onclick: () => location.reload() }, 'Повторить')))));
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }

})();
