/* Интерфейс инженера: одностраничное приложение на ванильном JS (ES2020).
 *
 * Сборщиков нет, внешних загрузок нет — файл подключается тегом <script defer>
 * и работает в изолированном контуре. Разделы файла:
 *
 *   1. утилиты и словари;
 *   2. клиент REST API;
 *   3. общие элементы: уведомления, модальные окна, индикатор длинных операций;
 *   4. шапка, темы, маршрутизация;
 *   5. экран «Письма»;
 *   6. экран письма — три панели (факты | отчёт | источники и замечания);
 *   7. экран «Библиотека»;
 *   8. экран «Метрики» и журнал действий;
 *   9. разметка ответа помощника (упрощённый Markdown);
 *  10. экран «Помощник» — три панели (разговоры | переписка | источники);
 *  11. личный кабинет;
 *  12. запуск.
 */

'use strict';

(function () {

    // =====================================================================
    // 1. Утилиты и словари
    // =====================================================================

    /* Состояния письма. Формулировки — как в журнале входящих: «принято»,
       а не «новый», «отправлено», а не «утверждён». */
    const CASE_STATUS = {
        new: 'принято',
        draft: 'в работе',
        review: 'на проверке',
        checked: 'проверен, к отправке',
        approved: 'отправлено',
        archived: 'в архиве',
    };

    /* Порядок движения письма: следующее состояние — соседнее справа.
       «Проверен» и «отправлено» — разные вещи: начальник согласился, но
       ответ ещё не ушёл. Между ними стоит работа исполнителя — отправить
       ответ и записать исходящий номер. */
    const CASE_FLOW = ['new', 'draft', 'review', 'checked', 'approved', 'archived'];
    /* Эти три состояния письму даёт ход отчёта, а не отметка в карточке. */
    const CASE_BY_FLOW = ['review', 'checked', 'approved'];

    /* Пределы полей карточки письма. Те же, что на сервере
       (api.MAX_CARD_FIELDS): поле не должно принимать то, что сервер потом
       отвергнет — человек уже напечатал. */
    const CARD_LIMIT = { title: 300, incoming_no: 60, outgoing_no: 60, tc_no: 60,
                         order_no: 60, note: 2000, outgoing_note: 2000,
                         group_no: 120 };

    const CASE_PRIORITY = { normal: 'обычный', high: 'важный', urgent: 'срочный' };

    /* Линии связи. Дублируем названия на случай, если справочник с сервера
       ещё не пришёл: в списке писем прочерк вместо «СЛС» читается как
       «линия не указана», а это разные вещи. */
    const LINE_TITLE = { sls: 'СЛС', rrls: 'РРЛС', kv: 'КВ', other: 'Другое' };

    /* Штатные должности. Права: до начальника группы включительно —
       администраторы, остальные ведут письма и отчёты. */
    const ROLE_LABEL = {
        owner: 'Создатель системы',
        head: 'Начальник отдела',
        deputy: 'Заместитель начальника отдела',
        lead: 'Начальник группы',
        senior: 'Старший инженер отдела',
        engineer: 'Инженер отдела',
    };

    /* Короткая форма для таблиц: полное название в столбце не помещается. */
    const ROLE_SHORT = {
        owner: 'создатель',
        head: 'нач. отдела',
        deputy: 'зам. нач. отдела',
        lead: 'нач. группы',
        senior: 'ст. инженер',
        engineer: 'инженер',
    };

    const ABSENCE_LABEL = {
        duty: 'дежурство',
        vacation: 'отпуск',
        sick: 'больничный',
        trip: 'командировка',
        study: 'учёба',
    };

    /* Путь отчёта: готовит исполнитель, проверяет начальник отдела или зам. */
    const REPORT_STATUS = {
        draft: 'в работе',
        review: 'на проверке',
        rework: 'требует исправления',
        approved: 'проверен',
    };

    /* Каким значком показывать состояние отчёта в списке и в карточке. */
    const REPORT_STATUS_TONE = {
        draft: '',
        review: 'badge--info',
        rework: 'badge--danger',
        approved: 'badge--ok',
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
        misc: 'прочее',
    };

    const LEVEL_LABEL = {
        error: 'Ошибки',
        warning: 'Предупреждения',
        info: 'Замечания',
    };

    const AUDIT_LABEL = {
        'auth.login': 'вход в систему',
        'auth.fail': 'неудачная попытка входа',
        'case.create': 'зарегистрировано письмо',
        'case.delete': 'удалено письмо',
        'case.facts.update': 'изменён факт-пакет',
        'case.send': 'ответ отправлен, записан исходящий номер',
        'case.send.withdraw': 'отозвана отправка ответа',
        'cases.reindex': 'перестроен указатель поиска по письмам',
        'report.generate': 'собран черновик отчёта',
        'report.section.edit': 'правка раздела',
        'report.section.save': 'сохранена секция',
        'report.section.regenerate': 'раздел написан заново',
        'report.section.restore': 'возвращён черновик модели',
        'report.submit': 'отчёт сдан на проверку',
        'report.rework': 'отчёт возвращён на исправление',
        'report.approve': 'отчёт отмечен проверенным',
        'report.approval.revoked': 'снята отметка о проверке',
        'report.withdraw': 'прежняя редакция снята с проверки',
        'report.upload': 'сдан готовый отчёт файлом',
        'report.export': 'выгрузка отчёта',
        'chat.attach': 'к разговору приложен файл',
        'library.status': 'изменена актуальность документа',
        'library.upload': 'загружен документ',
        'library.reindex': 'переиндексация библиотеки',
        'library.delete': 'удалён документ',
        'library.domain': 'изменено направление документа',
        'chat.ask': 'вопрос помощнику',
        'chat.delete': 'удалён разговор',
        'user.create': 'заведён сотрудник',
        'user.update': 'изменена запись сотрудника',
        'user.active': 'изменён доступ сотрудника',
        'user.password': 'смена пароля',
        'case.update': 'изменена карточка письма',
        'absence.add': 'отмечено дежурство или отсутствие',
        'absence.delete': 'снята отметка об отсутствии',
    };

    /** Примеры вопросов для пустого экрана помощника — по разным направлениям. */
    const CHAT_EXAMPLES = [
        { domain: 'satellite', text: 'Как закладывают запас на дождевое затухание в спутниковой линии Ku-диапазона?' },
        { domain: 'microwave', text: 'Что проверить на радиорелейном пролёте при частых кратковременных замираниях?' },
        { domain: 'protocols', text: 'Из каких полей состоит кадр HDLC и по какому полиному считается FCS?' },
        { domain: 'signal', text: 'Какой предел EVM допустим для QPSK и как он связан с коэффициентом битовых ошибок?' },
        { domain: 'method', text: 'По какой методике измеряется занимаемая полоса частот и какой процент мощности берут?' },
        { domain: 'hf', text: 'Как выбирают рабочую частоту на коротковолновой трассе в зависимости от времени суток?' },
        { domain: 'mobile', text: 'Что означают счётчики RSRP и SINR и при каких значениях абонент теряет соединение?' },
    ];

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

    /** Короткая устойчивая метка строки: нужна для ключей в localStorage. */
    function hashString(text) {
        let hash = 0;
        for (let index = 0; index < text.length; index += 1) {
            hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
        }
        return (hash >>> 0).toString(36);
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

    /** Дата ГГГГ-ММ-ДД → ДД.ММ.ГГГГ. Пустая строка остаётся пустой. */
    function fmtDate(value) {
        const text = String(value || '').slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return '';
        const parts = text.split('-');
        return parts[2] + '.' + parts[1] + '.' + parts[0];
    }

    /* Значки в кнопках-иконках: текстовый крестик читался как опечатка. */
    const GLYPHS = {
        edit: 'M11.4 2.6 13.4 4.6 5.5 12.5 2.5 13.5 3.5 10.5zM10 4l2 2',
        trash: 'M2.5 4.5h11M6.5 4.5V3h3v1.5M4 4.5l.7 9h6.6l.7-9M6.5 7v4M9.5 7v4',
        plus: 'M8 3.5v9M3.5 8h9',
        clip: 'M11.5 6.5 6.8 11.2a2.3 2.3 0 0 1-3.3-3.3l5.2-5.2a1.6 1.6 0 0 1 2.3 2.3l-5.2 5.2a.8.8 0 0 1-1.2-1.2l4.7-4.7',
        check: 'M3 8.5 6.5 12 13 4.5',
        close: 'M4 4l8 8M12 4l-8 8',
    };

    function iconGlyph(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 16 16');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.4');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.innerHTML = '<path d="' + (GLYPHS[name] || '') + '"/>';
        return svg;
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

    /** Поле пароля с кнопкой «показать»: вслепую его набирают с ошибками. */
    function passwordField(placeholder) {
        const input = h('input', { type: 'password', placeholder: placeholder || '' });
        const toggle = h('button', {
            class: 'btn btn--ghost btn--sm pw-toggle', type: 'button',
            title: 'Показать пароль',
            onclick: () => {
                const shown = input.type === 'text';
                input.type = shown ? 'password' : 'text';
                toggle.textContent = shown ? 'Показать' : 'Скрыть';
                toggle.title = shown ? 'Показать пароль' : 'Скрыть пароль';
                input.focus();
            },
        }, 'Показать');
        const box = h('div', { class: 'pw-field' }, input, toggle);
        box.input = input;
        return box;
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
        const next = encodeURIComponent(location.hash || '#/board');
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
        patch: (path, body) => request(path, { method: 'PATCH', body: body || {} }),
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
        const modal = h('div', {
            class: 'modal' + (options.narrow ? ' modal--narrow' : '')
                + (options.wide ? ' modal--wide' : ''),
        },
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
    /** Спросить одну строку. Для пароля — со скрытым вводом. */
    function promptDialog(options) {
        return new Promise((resolve) => {
            const box = options.password ? passwordField(options.placeholder) : null;
            // Замечание проверяющего в одну строку не помещается: это текст
            // «что исправить», а не значение поля.
            const input = box ? box.input : h(options.multiline ? 'textarea' : 'input',
                Object.assign(
                    { placeholder: options.placeholder || '', value: options.value || '' },
                    options.multiline ? { rows: '4' } : { type: 'text' }));
            let answered = false;
            const finish = (value) => {
                if (answered) return;
                answered = true;
                dialog.close();
                resolve(value);
            };
            input.addEventListener('keydown', (event) => {
                // В многострочном поле Enter — это перевод строки.
                if (event.key === 'Enter' && !options.multiline) finish(input.value);
            });
            const dialog = openModal({
                title: options.title || 'Введите значение',
                narrow: true,
                body: h('div', { class: 'form-grid' },
                    options.message ? h('div', { class: 'muted' }, options.message) : null,
                    box || input,
                    options.note ? h('div', { class: 'small muted' }, options.note) : null),
                footer: [
                    h('button', { class: 'btn', onclick: () => finish(null) }, 'Отмена'),
                    h('button', {
                        class: 'btn btn--primary', onclick: () => finish(input.value),
                    }, options.confirmText || 'Готово'),
                ],
                onClose: () => finish(null),
            });
            setTimeout(() => input.focus(), 0);
        });
    }

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
        config: { outlines: [], doc_types: [], llm: {} },
        // Поддержка форматов зависит от того, какие программы установлены
        // на этой машине, поэтому запрашивается у сервера, а не зашита.
        formats: null,
        route: { name: 'board', id: null },
    };

    /* Права приходят с сервера вместе с записью сотрудника: держать здесь
       второй список должностей — верный способ разойтись с бэкендом. */
    function canEdit() {
        return !!state.user;
    }

    function isAdmin() {
        return !!state.user && state.user.is_admin === true;
    }

    /** Может ли этот человек проверять отчёты: начальник отдела или зам.
     *
     * Это не то же самое, что права администратора: начальник группы заводит
     * людей, а отчёты проверяет не он.
     */
    function canReview() {
        return !!state.user && state.user.can_review === true;
    }

    function isOwner() {
        return !!state.user && state.user.is_owner === true;
    }

    function outlineFor(reportType) {
        return (state.config.outlines || []).find((item) => item.report_type === reportType) || null;
    }

    function reportTypeTitle(reportType) {
        const outline = outlineFor(reportType);
        return outline ? outline.title : reportType;
    }

    /* Короткое имя направления работы для узких мест — списков и колонок.
       Полные названия шаблонов начинаются одинаково («Технический отчёт по
       результатам анализа…»), и в закрытом списке видно только общее начало:
       выбрать не из чего. Короткое имя задаёт сам шаблон-план полем
       short_title, чтобы отдел называл направления своими словами. */
    function reportTypeShort(outline) {
        if (!outline) return '';
        return outline.short_title || outline.title || outline.report_type;
    }

    function docTypeLabel(value) {
        return DOC_TYPE_LABEL[value] || value;
    }

    function domains() {
        return state.config.domains || [];
    }

    /** Название направления по идентификатору; пустое значение — «все направления». */
    function domainTitle(value) {
        if (!value) return 'не указано';
        const found = domains().find((item) => item.id === value);
        return found ? found.title : value;
    }

    /** Выпадающий список направлений с первым пунктом «всё». */
    const DEFAULT_STATUSES = [
        { id: 'current', title: 'действующий' },
        { id: 'superseded', title: 'заменён' },
        { id: 'archived', title: 'архив' },
        { id: 'draft', title: 'проект' },
    ];

    function domainSelect(options) {
        const opts = options || {};
        return h('select', {
            title: opts.title || 'Направление техники',
            disabled: opts.disabled,
            onchange: (event) => opts.onchange && opts.onchange(event.target.value),
        }, h('option', { value: '', selected: !opts.value }, opts.anyLabel || 'Все направления'),
            domains().map((item) => h('option', {
                value: item.id, selected: opts.value === item.id,
            }, item.title)));
    }

    // -- темы ---------------------------------------------------------------

    const THEME_LABEL = { auto: 'Авто', light: 'Светлая', dark: 'Тёмная' };

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

    // -- изменяемые колонки таблиц ------------------------------------------

    /** Сделать колонки таблицы перетаскиваемыми по ширине.

        Ширины, подобранные разработчиком, подходят не всем: у одного длинные
        названия документов, у другого — длинные идентификаторы. Поэтому
        границу столбца можно тянуть, а выбранная ширина запоминается в
        браузере и переживает перезагрузку. Двойной щелчок по границе
        возвращает столбцу исходную ширину.
    */
    function makeResizable(table, key) {
        if (!table || table.dataset.resizable === '1') return table;
        table.dataset.resizable = '1';
        const headers = $$('thead th', table);
        if (headers.length < 2) return table;

        // В ключ входит состав заголовков: ширины хранятся по номеру
        // столбца, и после удаления или перестановки столбца сохранённые
        // значения съезжали на соседние. При смене набора начинаем заново.
        const shape = headers.map((th) => (th.textContent || '').trim()).join('|');
        const storageKey = 'reportgen.cols.' + key + '.' + hashString(shape);
        let saved = {};
        try {
            saved = JSON.parse(storageGet(storageKey, '{}')) || {};
        } catch (error) {
            saved = {};
        }

        headers.forEach((th, index) => {
            const width = saved[index];
            if (width) th.style.width = width + 'px';
            if (index === headers.length - 1) return;

            const grip = h('span', {
                class: 'col-grip',
                title: 'Потяните, чтобы изменить ширину. Двойной щелчок — вернуть исходную',
            });
            // Перетаскивание не должно превращаться в сортировку или щелчок
            // по заголовку, поэтому события гасим здесь же.
            grip.addEventListener('click', (event) => event.stopPropagation());
            grip.addEventListener('dblclick', (event) => {
                event.stopPropagation();
                th.style.width = '';
                delete saved[index];
                storageSet(storageKey, JSON.stringify(saved));
            });
            grip.addEventListener('pointerdown', (event) => {
                event.preventDefault();
                event.stopPropagation();
                const startX = event.clientX;
                const startWidth = th.getBoundingClientRect().width;
                table.classList.add('is-resizing');

                // Слушаем документ, а не саму ручку: указатель во время
                // перетаскивания уходит за её пределы, и захват указателя
                // ведёт себя по-разному в разных браузерах.
                const move = (moveEvent) => {
                    const next = Math.max(56, Math.round(startWidth + moveEvent.clientX - startX));
                    th.style.width = next + 'px';
                };
                const stop = () => {
                    table.classList.remove('is-resizing');
                    document.removeEventListener('pointermove', move, true);
                    document.removeEventListener('pointerup', stop, true);
                    document.removeEventListener('pointercancel', stop, true);
                    saved[index] = Math.round(th.getBoundingClientRect().width);
                    storageSet(storageKey, JSON.stringify(saved));
                };
                document.addEventListener('pointermove', move, true);
                document.addEventListener('pointerup', stop, true);
                document.addEventListener('pointercancel', stop, true);
            });
            th.appendChild(grip);
        });
        return table;
    }

    function applyTheme(mode) {
        const root = document.documentElement;
        if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
        else root.removeAttribute('data-theme');
    }

    // -- каркас: боковое меню и шапка ---------------------------------------

    /* Значки разделов. Рисуем контуром в одном стиле: заливка спорит
       с текстом, а готовых наборов в изолированном контуре нет. */
    const ICONS = {
        board: '<path d="M2.5 2.5h5v5h-5zM10 2.5h5.5v3h-5.5zM10 8h5.5v5.5h-5.5zM2.5 10h5v3.5h-5z"/>',
        letters: '<path d="M2 4.5h14v9H2zM2 4.5l7 5 7-5"/>',
        chat: '<path d="M2.5 3.5h13v8.5h-8L4 15v-3H2.5z"/>',
        library: '<path d="M3 2.5h3v13H3zM7 2.5h3v13H7zM11.4 3.2l2.8.8-3.2 12.5-2.8-.8z"/>',
        stats: '<path d="M2.5 13.5h13M4.5 13.5V8M8 13.5V3.5M11.5 13.5V6.5M15 13.5v-3"/>',
        users: '<path d="M6.2 8.5a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2zM1.8 15c0-2.4 2-4 4.4-4s4.4 1.6 4.4 4M11.5 4a2.3 2.3 0 0 1 0 4.6M13 11.2c1.6.5 2.7 1.8 2.7 3.8"/>',
        roster: '<path d="M2.5 4h13v11.5h-13zM2.5 7.5h13M6 2.5v3M12 2.5v3M5.5 10.5h2M8.5 10.5h2M11.5 10.5h2M5.5 13h2M8.5 13h2"/>',
        talks: '<path d="M2.5 3.5h10v7h-6l-4 3zM12.5 6h3v7h-2.5l-3 2.2V13"/>',
    };

    /** Разделы бокового меню. Порядок — от «что сегодня» к справочникам. */
    const SECTIONS = [
        { route: 'board', href: '#/board', title: 'Дашборд', icon: 'board' },
        { route: 'cases', href: '#/cases', title: 'Письма', icon: 'letters', count: 'letters' },
        { route: 'roster', href: '#/roster', title: 'Расход', icon: 'roster' },
        { route: 'talks', href: '#/talks', title: 'Сообщения', icon: 'talks', count: 'talks' },
        { route: 'chat', href: '#/chat', title: 'Помощник', icon: 'chat' },
        { route: 'library', href: '#/library', title: 'Библиотека', icon: 'library' },
        { route: 'stats', href: '#/stats', title: 'Метрики', icon: 'stats' },
        { route: 'users', href: '#/users', title: 'Сотрудники', icon: 'users', adminOnly: true },
    ];

    /** Какой пункт меню подсвечивать для вложенного экрана. */
    const SECTION_OF = { case: 'cases' };

    function icon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 18 18');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.4');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.innerHTML = ICONS[name] || '';
        return svg;
    }

    function buildNav() {
        const nav = $('#nav');
        if (!nav) return;
        clear(nav);
        SECTIONS.forEach((section) => {
            if (section.adminOnly && !isAdmin()) return;
            const link = h('a', {
                href: section.href,
                dataset: { route: section.route },
                title: section.title,
            }, icon(section.icon), h('span', {}, section.title));
            if (section.count) {
                link.appendChild(h('b', { class: 'side-count', hidden: true, dataset: { count: section.count } }));
            }
            nav.appendChild(link);
        });
        setActiveNav(state.route ? state.route.name : 'board');
    }

    /** Показать счётчик у раздела: сколько писем в работе и сколько просрочено. */
    function setNavCount(name, value, late) {
        const badge = $('#nav .side-count[data-count="' + name + '"]');
        if (!badge) return;
        const number = Number(value) || 0;
        badge.hidden = number === 0;
        badge.textContent = String(number);
        badge.classList.toggle('is-late', !!late);
        badge.title = late ? 'из них просрочено: ' + late : '';
    }

    /* Эмблема отдела: настоящий знак файлом, отклик — состояние, а не
       движение. Наведение и нажатие делает CSS. Здесь только одна вещь,
       которой CSS не умеет: одинаковое поведение при нажатии для мыши,
       касания и клавиатуры. Не выполнится — знак всё равно откликается на
       наведение и остаётся ссылкой на дашборд. */

    function wakeEmblem() {
        const mark = $('#emblem');
        if (!mark || mark.dataset.ready) return;
        mark.dataset.ready = '1';

        const hold = () => mark.classList.add('emblem--held');
        const release = () => mark.classList.remove('emblem--held');

        mark.addEventListener('pointerleave', release);
        mark.addEventListener('pointerdown', hold);
        mark.addEventListener('pointerup', release);
        mark.addEventListener('pointercancel', release);
        mark.addEventListener('keydown', (event) => {
            if (event.key === ' ' || event.key === 'Enter') hold();
        });
        mark.addEventListener('keyup', release);
        mark.addEventListener('blur', release);
    }

    /** Название отдела. Оно одно на всю систему и живёт в настройках. */
    function brandName() {
        const brand = (state.config && state.config.brand) || {};
        return brand.name || '2 специальный отдел';
    }

    // =====================================================================
    // 4а. Уведомления
    // =====================================================================

    /* Что человеку нужно знать: начальник вернул отчёт, письмо назначили на
       вас, вас вызывают в кабинет, пришло сообщение.

       Машина изолирована — ни почты, ни телефона, — и единственное место,
       куда можно положить «вам сообщение», это та же база. Экран спрашивает
       её раз в двадцать секунд: постоянного соединения в проекте нет и
       заводить его ради колокольчика незачем.

       Звук делаем сами через Web Audio: файла со звонком нет и быть не
       может — ни одной внешней загрузки в системе. Звучит только громкое:
       вызов в кабинет и возврат отчёта. Остальное ждёт молча. */

    const NOTICE_POLL_MS = 20000;
    const NOTICE_ICON = {
        'report.rework': '!',
        'report.review': '→',
        'report.approved': '✓',
        'case.assigned': '✉',
        'case.note': '…',
        'case.sent': '✓',
        'call': '☎',
        'message': '✉',
        'user.approved': '✓',
    };

    const notices = { items: [], unseen: 0, messages: 0, timer: null, seenLoud: 0 };

    /** Короткий сигнал. Ни файла, ни библиотеки: два тона встроенным синтезом. */
    /* Звук уведомления. Браузер не даёт странице звучать, пока человек её не
       тронул: свежесозданный AudioContext стоит «приостановленным», и всё,
       что в него расписали, уходит в тишину. Поэтому контекст держим один,
       будим его первым же касанием страницы и на всякий случай будим ещё раз
       перед самим сигналом. */
    let audioCtx = null;

    function wakeAudio() {
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (!Audio) return null;
        try {
            if (!audioCtx) audioCtx = new Audio();
            if (audioCtx.state === 'suspended') audioCtx.resume();
        } catch (error) {
            audioCtx = null;      // звук — вещь необязательная
        }
        return audioCtx;
    }

    function armAudio() {
        // Одного касания хватает на весь сеанс — дальше слушать незачем.
        const once = { once: true, passive: true };
        ['pointerdown', 'keydown'].forEach((name) =>
            document.addEventListener(name, wakeAudio, once));
    }

    function playAlert() {
        const ctx = wakeAudio();
        if (!ctx) return;
        try {
            const now = ctx.currentTime;
            [880, 1170].forEach((hz, index) => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = hz;
                // Резкий старт и мягкий спад: щелчка нет, а слышно сразу.
                gain.gain.setValueAtTime(0.0001, now + index * 0.18);
                gain.gain.exponentialRampToValueAtTime(0.14, now + index * 0.18 + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.18 + 0.16);
                osc.connect(gain).connect(ctx.destination);
                osc.start(now + index * 0.18);
                osc.stop(now + index * 0.18 + 0.18);
            });
        } catch (error) {
            /* звук — вещь необязательная: уведомление и так на экране */
        }
    }

    async function pollNotices(loud) {
        if (!state.user) return;
        let data;
        try {
            data = await api.get('/api/notifications?limit=50');
        } catch (error) {
            return;                     // сеть моргнула — спросим через двадцать секунд
        }
        const fresh = (data.items || []).filter((item) => !item.seen && item.loud);
        notices.items = data.items || [];
        notices.unseen = data.unseen || 0;
        notices.messages = data.messages || 0;
        paintBell();

        // Звук и всплывающее — только на новое громкое и только если это не
        // первая загрузка страницы: заходить утром под звонок недельной
        // давности человеку незачем.
        const newest = fresh.length ? fresh[0].id : 0;
        if (loud && newest && newest > notices.seenLoud) {
            playAlert();
            toast(fresh[0].title, 'info', 8000);
        }
        if (newest > notices.seenLoud) notices.seenLoud = newest;
    }

    function paintBell() {
        const bell = $('#bell');
        const count = $('#bell-count');
        if (!bell || !count) return;
        bell.hidden = !state.user;
        const total = (notices.unseen || 0) + (notices.messages || 0);
        count.hidden = !total;
        count.textContent = total > 99 ? '99+' : String(total);
        bell.classList.toggle('has-loud',
            notices.items.some((item) => !item.seen && item.loud));
        bell.title = total ? 'Уведомлений: ' + total : 'Уведомлений нет';
    }

    function openNotices() {
        const list = h('div', { class: 'notice-list' });

        function draw() {
            clear(list);
            if (!notices.items.length) {
                list.appendChild(h('div', { class: 'muted' }, 'Уведомлений нет.'));
                return;
            }
            notices.items.forEach((item) => list.appendChild(h('div', {
                class: 'notice' + (item.seen ? '' : ' is-new')
                    + (item.loud ? ' is-loud' : ''),
            },
                h('span', { class: 'notice-mark' }, NOTICE_ICON[item.kind] || '•'),
                h('div', { class: 'notice-body' },
                    h('b', {}, item.title),
                    item.body ? h('div', { class: 'small' }, item.body) : null,
                    h('div', { class: 'small faint' },
                        fmtDateTime(item.created_at)
                        + (item.from_name ? ' · ' + item.from_name : ''))),
                item.link ? h('button', {
                    class: 'btn btn--sm',
                    onclick: async () => {
                        await api.post('/api/notifications/read', { id: item.id });
                        dialog.close();
                        navigate(item.link);
                        pollNotices(false);
                    },
                }, 'Открыть') : null)));
        }
        draw();

        const dialog = openModal({
            title: 'Уведомления',
            body: [list],
            footer: [
                h('button', {
                    class: 'btn', onclick: async () => {
                        await api.del('/api/notifications');
                        notices.items = [];
                        notices.unseen = 0;
                        paintBell();
                        draw();
                    },
                }, 'Очистить'),
                h('span', { class: 'spacer' }),
                h('button', {
                    class: 'btn btn--primary', onclick: async () => {
                        await api.post('/api/notifications/read', {});
                        await pollNotices(false);
                        draw();
                    },
                }, 'Всё прочитано'),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Закрыть'),
            ],
        });
    }

    function startNotices() {
        const bell = $('#bell');
        if (bell && !bell.dataset.ready) {
            bell.dataset.ready = '1';
            bell.onclick = () => openNotices();
        }
        if (notices.timer) return;
        armAudio();
        // Первый опрос — молча: человек только вошёл, звонить ему нечем.
        pollNotices(false);
        notices.timer = setInterval(() => pollNotices(true), NOTICE_POLL_MS);
    }

    function renderChrome() {
        const brand = (state.config && state.config.brand) || null;
        if (brand && brand.name) {
            const short = $('#brand-short');
            const full = $('#brand-name');
            if (short) short.textContent = brand.short || brand.name;
            if (full) full.textContent = brand.name;
            const home = $('.side-brand');
            if (home) home.title = brand.name
                + (brand.subtitle ? ' — ' + brand.subtitle : '');
            document.title = brand.name;
        }
        if (brand && typeof brand.accent === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(brand.accent)) {
            document.documentElement.style.setProperty('--accent', brand.accent);
        }

        // Модель — точка состояния. Пока состояние не выяснено, точка серая
        // и подписана «проверяем»: зелёная по умолчанию врала бы про
        // неподнятый llama-server, и инженер узнавал бы об этом, только
        // прождав минуту после первого вопроса.
        const dot = $('#llm-info');
        if (dot && !dot.dataset.asked) {
            dot.dataset.asked = '1';
            const llm = state.config.llm || {};
            dot.title = 'Проверяем сервер модели' + (llm.model ? ': ' + llm.model : '');
            api.get('/api/llm/status').then((data) => {
                dot.classList.toggle('is-up', !!data.available);
                dot.classList.toggle('is-down', !data.available);
                dot.title = (data.available
                    ? 'Модель отвечает'
                    : 'Сервер модели не отвечает — запустите start-llm.ps1') +
                    (data.model ? ': ' + data.model : '') +
                    (data.base_url ? ' (' + data.base_url + ')' : '');
            }).catch(() => {
                dot.title = 'Состояние сервера модели неизвестно';
            });
        }

        buildNav();
        wakeEmblem();
        startNotices();
        paintBell();

        const chip = $('#user-chip');
        const logout = $('#logout-btn');
        if (state.user) {
            chip.hidden = false;
            const name = state.user.full_name || state.user.login;
            $('#user-name').textContent = name;
            $('#user-initials').textContent = initials(name);
            $('#user-role').textContent = roleLabel(state.user.role);
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

        bindSidebar();
    }

    /** Инициалы для кружка рядом с именем: «Петров И. С.» → «ПИ». */
    function initials(name) {
        const words = String(name || '').trim().split(/[\s.]+/).filter(Boolean);
        if (!words.length) return '—';
        const first = words[0].charAt(0);
        const second = words.length > 1 ? words[1].charAt(0) : '';
        return (first + second).toUpperCase();
    }

    function bindSidebar() {
        const collapse = $('#side-btn');
        if (collapse && !collapse.dataset.bound) {
            collapse.dataset.bound = '1';
            collapse.onclick = () => {
                const min = !document.body.classList.contains('side-min');
                document.body.classList.toggle('side-min', min);
                storageSet('rg-side-min', min ? '1' : '0');
                collapse.title = min ? 'Развернуть меню' : 'Свернуть меню';
            };
        }
        const open = $('#side-open');
        if (open && !open.dataset.bound) {
            open.dataset.bound = '1';
            open.onclick = () => document.body.classList.toggle('side-open');
        }
        const scrim = $('#side-scrim');
        if (scrim && !scrim.dataset.bound) {
            scrim.dataset.bound = '1';
            scrim.onclick = () => document.body.classList.remove('side-open');
        }
    }

    function roleLabel(role) {
        return ROLE_LABEL[role] || role;
    }

    function setActiveNav(name) {
        const section = SECTION_OF[name] || name;
        $$('#nav a').forEach((link) => {
            link.classList.toggle('is-active', link.dataset.route === section);
        });
        const chip = $('#user-chip');
        if (chip) chip.classList.toggle('is-active', name === 'me');
        const title = $('#topbar-title');
        if (title) {
            const found = SECTIONS.filter((item) => item.route === section)[0];
            title.textContent = name === 'me' ? 'Личный кабинет' : (found ? found.title : '');
        }
        // На узком экране меню закрывается сразу после выбора раздела.
        document.body.classList.remove('side-open');
    }

    // -- маршрутизация ------------------------------------------------------

    let currentHash = '#/board';
    let restoringHash = false;

    function parseHash(hash) {
        const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean);
        if (!parts.length) return { name: 'board', id: null };
        if (parts[0] === 'case' && parts[1]) return { name: 'case', id: parts[1] };
        if (parts[0] === 'chat') return { name: 'chat', id: parts[1] ? decodeURIComponent(parts[1]) : null };
        if (parts[0] === 'talks') return { name: 'talks', id: parts[1] ? parts[1] : null };
        if (parts[0] === 'library' && parts.length > 1) {
            // Идентификатор документа — путь вида «standards/obw-method».
            return { name: 'library', id: parts.slice(1).map(decodeURIComponent).join('/') };
        }
        if (['board', 'cases', 'roster', 'talks', 'library', 'stats', 'me', 'users'].indexOf(parts[0]) !== -1) {
            return { name: parts[0], id: null };
        }
        return { name: 'board', id: null };
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
        const next = location.hash || '#/board';
        if (hasUnsaved()) {
            const ok = await confirmDialog({
                title: 'Есть несохранённые правки',
                message: 'В письме остались несохранённые изменения (' + unsavedMessage() +
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
        // Уходя с экрана помощника, отпускаем узлы страницы, но генерацию
        // не трогаем: ответ дописывается в фоне и ждёт возвращения.
        detachChat();
        stopTalkPoll();
        state.route = route;
        setActiveNav(route.name);
        const view = $('#view');
        clear(view);
        view.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка…'));
        try {
            if (route.name === 'board') await renderBoard(view);
            else if (route.name === 'cases') await renderCases(view);
            else if (route.name === 'case') await renderCase(view, route.id);
            else if (route.name === 'library') await renderLibrary(view, route.id);
            else if (route.name === 'stats') await renderStats(view);
            else if (route.name === 'roster') await renderRoster(view);
            else if (route.name === 'chat') await renderChat(view, route.id);
            else if (route.name === 'talks') await renderTalks(view, route.id);
            else if (route.name === 'me') await renderMe(view);
            else if (route.name === 'users') await renderUsers(view);
        } catch (error) {
            if (error instanceof ApiError && error.status === 401) return;
            clear(view);
            view.appendChild(h('div', { class: 'page' },
                h('div', { class: 'card card-pad' },
                    h('h3', { style: { color: 'var(--danger)', marginBottom: '6px' } }, 'Не удалось открыть раздел'),
                    h('div', { class: 'muted' }, errorText(error)),
                    h('div', { class: 'btn-row', style: { marginTop: '12px' } },
                        h('button', { class: 'btn', onclick: () => renderRoute(state.route) }, 'Повторить'),
                        h('a', { class: 'btn', href: '#/board' }, 'На дашборд')))));
        }
    }

    function navigate(hash) {
        if (location.hash === hash) onHashChange();
        else location.hash = hash;
    }

    /** Сменить адрес, не перерисовывая экран: нужно, чтобы поток ответа не оборвался. */
    function replaceHash(hash) {
        if (location.hash === hash) return;
        restoringHash = true;
        currentHash = hash;
        location.hash = hash;
    }

    // =====================================================================
    // 5. Экран «Письма»
    // =====================================================================

    /* Раздел ведёт письма отдела. У письма есть входящий номер, номер
       группы (откуда пришло), срок ответа, исполнитель и один отчёт —
       подготовленный ответ. Круг замыкается исходящим номером: под ним
       ответ ушёл адресату, и только тогда письмо считается закрытым. */

    const casesState = {
        view: 'open',       // open | overdue | mine | all | архивные состояния
        query: '',
        limit: 100,
        offset: 0,
        total: 0,
        open: 0,
        overdue: 0,
        today: '',
        items: [],
        staff: [],
    };

    /* Наборы писем на кнопках-фильтрах. */
    const CASE_VIEWS = [
        { id: 'open', title: 'В работе', params: { status: 'open' } },
        { id: 'overdue', title: 'Просроченные', params: { overdue: '1' } },
        // Начальнику это первый набор, за которым он сюда заходит.
        { id: 'review', title: 'На проверке', params: { status: 'review' } },
        // А это первый набор исполнителя: начальник проверил, ответ ещё не ушёл.
        { id: 'checked', title: 'К отправке', params: { status: 'checked' } },
        { id: 'mine', title: 'Мои', params: { status: 'open', mine: true } },
        { id: 'approved', title: 'Отправленные', params: { status: 'approved' } },
        { id: 'all', title: 'Все', params: {} },
    ];

    /** Список сотрудников для выбора исполнителя. Читается раз на сеанс.
     *  Доступен всем: взять письмо на себя вправе любой инженер. */
    async function staffList() {
        if (casesState.staff.length) return casesState.staff;
        try {
            const data = await api.get('/api/staff');
            casesState.staff = data.items || [];
        } catch (error) {
            casesState.staff = [];
        }
        return casesState.staff;
    }

    async function renderCases(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        const tabs = h('div', { class: 'seg' });
        const searchInput = h('input', {
            type: 'search', class: 'field-search',
            placeholder: 'Номер, описание или слово из отчёта',
            title: 'Ищет по учётному и входящему номеру, исходящему номеру, '
                + 'описанию, номеру ТС, линии связи, номеру группы, примечанию '
                + '— и по тексту самих отчётов. Слова ищутся по основе: '
                + '«помеха» найдёт и «помехи».',
            value: casesState.query,
            oninput: debounce((event) => {
                casesState.query = event.target.value.trim();
                casesState.offset = 0;
                loadCases();
            }, 250),
        });

        const tableBox = h('div', { class: 'card' });
        const footer = h('div', { class: 'toolbar', style: { marginTop: '12px' } });

        append(page, [
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' }, 'Входящие письма и подготовленные ответы'),
                h('div', { class: 'page-head-actions' },
                    h('button', { class: 'btn', onclick: () => loadCases() }, 'Обновить'),
                    /* Указатель поиска строится сам, но перестроение может
                       оборваться, и тогда часть писем не находится. Кнопка
                       на такой случай — как и у библиотеки. */
                    isAdmin() ? h('button', {
                        class: 'btn btn--sm',
                        title: 'Перестроить указатель поиска по письмам, если что-то '
                            + 'перестало находиться',
                        onclick: () => reindexCases(),
                    }, 'Указатель поиска') : null,
                    canEdit() ? h('button', {
                        class: 'btn',
                        title: 'Загрузить свой готовый отчёт файлом на проверку начальнику',
                        onclick: () => openUploadReportDialog(loadCases),
                    }, 'Сдать готовый отчёт') : null,
                    canEdit() ? h('button', {
                        class: 'btn btn--primary', onclick: () => openNewCaseDialog(),
                    }, 'Зарегистрировать письмо') : null)),
            h('div', { class: 'toolbar' }, tabs, h('span', { class: 'grow' }), searchInput),
            tableBox,
            footer,
        ]);

        function renderTabs() {
            clear(tabs);
            CASE_VIEWS.forEach((item) => {
                let badge = null;
                if (item.id === 'open' && casesState.open) badge = String(casesState.open);
                if (item.id === 'overdue' && casesState.overdue) badge = String(casesState.overdue);
                tabs.appendChild(h('button', {
                    class: 'seg-item' + (casesState.view === item.id ? ' is-active' : '') +
                        (item.id === 'overdue' && casesState.overdue ? ' is-late' : ''),
                    onclick: () => {
                        casesState.view = item.id;
                        casesState.offset = 0;
                        loadCases();
                    },
                }, item.title, badge ? h('b', {}, badge) : null));
            });
        }

        /** Кнопка «Сдать готовый отчёт» и её диалог: реквизиты и файл. */
    /** Перестроить указатель поиска по письмам. Право администратора. */
    async function reindexCases() {
        const ok = await confirmDialog({
            title: 'Перестроить указатель поиска',
            message: 'Указатель по письмам и текстам отчётов будет собран заново.',
            note: 'Обычно он обновляется сам при каждой правке. Кнопка нужна, '
                + 'если что-то перестало находиться поиском.',
            confirmText: 'Перестроить',
        });
        if (!ok) return;
        try {
            const data = await withOverlay('Перестроение указателя',
                'Собираем реквизиты писем и тексты отчётов заново.',
                () => api.post('/api/cases/reindex', {}));
            toast('Указатель перестроен, писем: ' + (data.cases || 0), 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    async function openUploadReportDialog(after) {
        const staff = await staffList();
        const me = state.user || {};
        const incoming = h('input', {
            type: 'text', placeholder: 'ВХ-2026-0423',
            maxLength: CARD_LIMIT.incoming_no });
        const group = h('input', {
            type: 'text', placeholder: '1274 или 1-я группа',
            maxLength: CARD_LIMIT.group_no });
        const title = h('input', {
            type: 'text', placeholder: 'о чём письмо', maxLength: CARD_LIMIT.title });
        const incomingDate = h('input', { type: 'date' });
        const deadline = h('input', { type: 'date' });
        const priority = h('select', {}, ...Object.keys(CASE_PRIORITY).map((id) =>
            h('option', { value: id }, CASE_PRIORITY[id])));
        const assignee = h('select', {},
            h('option', { value: String(me.id || '') }, (me.full_name || me.login || 'я') + ' (я)'),
            ...staff.filter((person) => person.id !== me.id).map((person) =>
                h('option', { value: String(person.id) },
                    person.full_name || person.login)));
        /* Направление работы. Сданный файлом отчёт по шаблону не собирается,
           но письмо без направления не найти в отделе по нужной теме — а
           брать первое попавшееся значит подписать письмо чужой темой. */
        const outlines = state.config.outlines || [];
        const kind = h('select', {}, outlines.map((outline) =>
            h('option', { value: outline.report_type, title: outline.title },
                reportTypeShort(outline))));
        kind.disabled = !outlines.length;
        // Системную кнопку выбора файла прячем: она подписана по-английски
        // и в остальном интерфейсе такой нет.
        const chosen = h('span', { class: 'small muted' }, 'файл не выбран');
        const picker = h('input', {
            type: 'file',
            accept: '.docx,.doc,.pdf,.rtf,.odt,.md,.txt',
            style: { display: 'none' },
            onchange: () => {
                const file = (picker.files || [])[0];
                chosen.textContent = file ? file.name : 'файл не выбран';
                chosen.className = file ? 'small' : 'small muted';
            },
        });
        const pickButton = h('div', { class: 'file-pick' },
            h('button', {
                class: 'btn btn--sm', type: 'button',
                onclick: () => picker.click(),
            }, iconGlyph('clip'), 'Выбрать файл'),
            chosen, picker);

        const dialog = openModal({
            title: 'Сдать готовый отчёт',
            body: h('div', { class: 'form-grid' },
                h('div', { class: 'muted', style: { gridColumn: '1 / -1' } },
                    'Отчёт уйдёт на проверку начальнику отдела. Числа в нём система '
                    + 'не сверяет: факт-пакета за таким отчётом нет, читает его человек.'),
                h('label', { class: 'field' }, 'Файл отчёта', pickButton),
                h('label', { class: 'field' }, 'Входящий номер', incoming),
                h('label', { class: 'field' }, 'Номер группы', group),
                h('label', { class: 'field' }, 'Описание', title),
                h('label', { class: 'field' }, 'Направление работы', kind),
                h('label', { class: 'field' }, 'Дата письма', incomingDate),
                h('label', { class: 'field' }, 'Срок ответа', deadline),
                h('label', { class: 'field' }, 'Важность', priority),
                h('label', { class: 'field' }, 'Исполнитель', assignee)),
            footer: [
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                h('button', { class: 'btn btn--primary', onclick: () => send() }, 'Сдать на проверку'),
            ],
        });
        setTimeout(() => incoming.focus(), 30);

        async function send() {
            const file = (picker.files || [])[0];
            if (!file) { toast('Выберите файл отчёта', 'error'); return; }
            if (!incoming.value.trim()) { toast('Укажите входящий номер', 'error'); return; }
            const form = new FormData();
            form.append('file', file);
            form.append('case_id', incoming.value.trim());
            form.append('incoming_no', incoming.value.trim());
            form.append('group_no', group.value.trim());
            form.append('title', title.value.trim());
            form.append('report_type', kind.value || '');
            form.append('incoming_date', incomingDate.value || '');
            form.append('deadline', deadline.value || '');
            form.append('priority', priority.value);
            form.append('assignee_id', assignee.value || '');
            try {
                const data = await uploadFile('/api/reports/upload', form);
                dialog.close();
                toast(data.note
                    ? 'Отчёт сдан на проверку. Текст прочитать не удалось: ' + data.note
                    : 'Отчёт сдан на проверку', data.note ? 'info' : 'ok', 6000);
                if (after) after();
            } catch (error) {
                toastError(error);
            }
        }
    }

    async function loadCases() {
            renderTabs();
            clear(tableBox);
            tableBox.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' })));
            const chosen = CASE_VIEWS.filter((item) => item.id === casesState.view)[0] || CASE_VIEWS[0];
            const params = ['limit=' + casesState.limit, 'offset=' + casesState.offset];
            if (chosen.params.status) params.push('status=' + chosen.params.status);
            if (chosen.params.overdue) params.push('overdue=1');
            if (chosen.params.mine && state.user) params.push('assignee=' + state.user.id);
            if (casesState.query) params.push('q=' + encodeURIComponent(casesState.query));
            try {
                const data = await api.get('/api/cases?' + params.join('&'));
                casesState.items = data.items || [];
                casesState.total = data.total || 0;
                casesState.open = data.open || 0;
                casesState.overdue = data.overdue || 0;
                casesState.today = data.today || '';
                setNavCount('letters', casesState.open, casesState.overdue);
                renderTabs();
                renderCasesTable();
            } catch (error) {
                clear(tableBox);
                tableBox.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        function renderCasesTable() {
            const items = casesState.items;
            clear(tableBox);
            if (!items.length) {
                tableBox.appendChild(h('div', { class: 'empty' },
                    h('h3', {}, casesState.query ? 'Ничего не найдено' : 'Писем нет'),
                    h('div', { class: 'muted' }, casesState.query
                        ? 'Проверьте строку поиска или выберите другой набор.'
                        : 'Зарегистрируйте входящее письмо, чтобы начать работу.')));
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
                        h('td', { class: 'mono nowrap' }, item.incoming_no || item.case_id),
                        h('td', {}, h('div', {}, item.title || h('span', { class: 'faint' }, 'без описания')),
                            item.priority && item.priority !== 'normal'
                                ? h('span', { class: 'tag tag--' + item.priority },
                                    CASE_PRIORITY[item.priority]) : null,
                            // Искомого слова не видно ни в теме, ни в номерах —
                            // значит, письмо нашлось по тексту отчёта. Без
                            // пометки строка в выдаче выглядит случайной.
                            item.found_in_report
                                ? h('span', { class: 'tag', title: 'Искомое слово нашлось '
                                    + 'в тексте отчёта по этому письму' }, 'в тексте отчёта')
                                : null),
                        // Линия связи и номер средства: по ним в отделе
                        // отбирают письма своего хозяйства.
                        h('td', { class: 'small nowrap' },
                            item.line_type
                                ? h('span', { class: 'tag tag--line' },
                                    LINE_TITLE[item.line_type] || item.line_type)
                                : h('span', { class: 'faint' }, '—'),
                            item.tc_no ? h('div', { class: 'mono small muted' }, item.tc_no) : null),
                        h('td', { class: 'small nowrap' },
                            item.group_no || h('span', { class: 'faint' }, '—')),
                        h('td', { class: 'small nowrap' },
                            item.assignee_name || h('span', { class: 'faint' }, 'не назначен')),
                        h('td', { class: 'nowrap' }, deadlineCell(item)),
                        h('td', {}, statusBadge(item.status)),
                        // Исходящий номер — вторая половина учёта: чем ответили.
                        h('td', { class: 'mono small nowrap',
                            title: item.outgoing_date
                                ? 'Ответ отправлен ' + fmtDate(item.outgoing_date)
                                    + (item.sent_by_name ? ', ' + item.sent_by_name : '')
                                : '' },
                            item.outgoing_no || h('span', { class: 'faint' }, '—')),
                        h('td', { class: 'small muted nowrap' }, fmtDate(item.incoming_date) || '—'),
                        h('td', { class: 'row-actions nowrap' },
                            canEdit() ? h('button', {
                                class: 'btn btn--icon',
                                title: 'Карточка письма: исполнитель, срок, состояние',
                                onclick: () => openCaseCard(item, loadCases),
                            }, iconGlyph('edit')) : null,
                            canDeleteCase(item) ? h('button', {
                                class: 'btn btn--icon btn--danger-hover',
                                title: isAdmin()
                                    ? 'Удалить письмо вместе с отчётами'
                                    : 'Убрать ошибочно заведённое письмо',
                                onclick: () => deleteCase(item, loadCases),
                            }, iconGlyph('trash')) : null)));
                });
                tableBox.appendChild(h('div', { class: 'table-scroll' },
                    h('table', { class: 'grid grid--letters' },
                        h('thead', {}, h('tr', {},
                            h('th', {}, 'Входящий'),
                            h('th', {}, 'Описание'),
                            h('th', {}, 'Линия · ТС'),
                            h('th', {}, 'Номер группы'),
                            h('th', {}, 'Исполнитель'),
                            h('th', {}, 'Срок ответа'),
                            h('th', {}, 'Состояние'),
                            h('th', {}, 'Исходящий'),
                            h('th', {}, 'Дата письма'),
                            h('th', {}))),
                        body)));
            }

            clear(footer);
            const from = casesState.total ? casesState.offset + 1 : 0;
            const to = casesState.offset + items.length;
            append(footer, [
                h('span', { class: 'small muted' }, 'показаны ' + from + '–' + to + ' из ' + casesState.total),
                h('span', { class: 'grow' }),
                h('button', {
                    class: 'btn btn--sm', disabled: casesState.offset <= 0,
                    onclick: () => {
                        casesState.offset = Math.max(0, casesState.offset - casesState.limit);
                        loadCases();
                    },
                }, 'Назад'),
                h('button', {
                    class: 'btn btn--sm', disabled: to >= casesState.total,
                    onclick: () => {
                        casesState.offset += casesState.limit;
                        loadCases();
                    },
                }, 'Дальше'),
            ]);
        }

        await loadCases();
    }

    /** Срок с пометкой: просрочен, сегодня-завтра или спокойно. */
    function deadlineCell(item) {
        if (!item.deadline) return h('span', { class: 'faint' }, 'не задан');
        const today = casesState.today || todayIso();
        const done = item.status === 'approved' || item.status === 'archived';
        let cls = 'due';
        let note = '';
        if (!done && item.deadline < today) {
            cls = 'due due--late';
            const late = daysBetween(item.deadline, today);
            note = 'просрочено на ' + late + ' ' + plural(late, 'день', 'дня', 'дней');
        } else if (!done && daysBetween(today, item.deadline) <= 2) {
            cls = 'due due--soon';
            const left = daysBetween(today, item.deadline);
            note = 'осталось ' + left + ' ' + plural(left, 'день', 'дня', 'дней');
        }
        return h('span', { class: cls, title: note }, fmtDate(item.deadline));
    }

    function todayIso() {
        const now = new Date();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        return now.getFullYear() + '-' + month + '-' + String(now.getDate()).padStart(2, '0');
    }

    function daysBetween(from, to) {
        const a = Date.parse(from + 'T00:00:00');
        const b = Date.parse(to + 'T00:00:00');
        if (isNaN(a) || isNaN(b)) return 0;
        return Math.round((b - a) / 86400000);
    }

    function statusBadge(status) {
        const kind = { approved: 'ok', review: 'warn', archived: '', draft: 'info', new: 'accent' }[status];
        return h('span', { class: 'badge' + (kind ? ' badge--' + kind : '') }, CASE_STATUS[status] || status);
    }

    /** Карточка письма: исполнитель, срок, состояние, приоритет — без ухода со списка. */
    async function openCaseCard(item, after) {
        const staff = await staffList();
        const assignee = h('select', {},
            h('option', { value: '' }, 'не назначен'),
            staff.map((person) => h('option', {
                value: String(person.id),
                selected: person.id === item.assignee_id,
            }, (person.full_name || person.login) + ' — ' + (ROLE_SHORT[person.role] || person.role))));
        if (!staff.length) {
            assignee.disabled = true;
            assignee.title = 'Список сотрудников получить не удалось';
        }

        const deadline = h('input', { type: 'date', value: item.deadline || '' });
        const incomingNo = h('input', {
            type: 'text', class: 'mono', value: item.incoming_no || '',
            maxLength: CARD_LIMIT.incoming_no });
        const incomingDate = h('input', { type: 'date', value: item.incoming_date || '' });
        const priority = h('select', {}, Object.keys(CASE_PRIORITY).map((key) =>
            h('option', { value: key, selected: (item.priority || 'normal') === key },
                CASE_PRIORITY[key])));
        /* «На проверке» и «отправлено» письму даёт проверка отчёта: сдали —
           «на проверке», отметили проверенным — «отправлено». Руками их не
           выставляют, иначе письмо уходит из работы мимо начальника. Пока по
           письму нет ни одного отчёта, подменять нечего — на такое письмо
           ответили мимо системы, и отметить его можно. */
        const byFlow = Number(item.reports_count || 0) > 0;
        const status = h('select', {}, CASE_FLOW.map((key) => {
            const locked = byFlow && CASE_BY_FLOW.includes(key) && item.status !== key;
            return h('option', {
                value: key,
                selected: item.status === key,
                disabled: locked,
                title: locked ? 'Это состояние письмо получает от проверки отчёта' : '',
            }, CASE_STATUS[key] + (locked ? ' — по отчёту' : ''));
        }));
        const groupInput = h('input', {
            type: 'text', maxLength: CARD_LIMIT.group_no,
            placeholder: '1274 или 1-я группа', value: item.group_no || '',
            title: 'Откуда пришло письмо. Пишите как принято в отделе: 1274, 12/345, в/ч 74326, «2-я группа связи»',
        });
        const titleInput = h('textarea', {
            class: 'field-area', rows: 2, maxLength: CARD_LIMIT.title,
            placeholder: 'о чём письмо' }, item.title || '');
        const tcInput = h('input', {
            type: 'text', class: 'mono', maxLength: CARD_LIMIT.tc_no,
            placeholder: 'ТС-1274-03', value: item.tc_no || '' });
        const tcDate = h('input', { type: 'date', value: item.tc_date || '' });
        const orderNo = h('input', {
            type: 'text', class: 'mono', maxLength: CARD_LIMIT.order_no,
            placeholder: 'У-2026-14', value: item.order_no || '' });
        const orderDate = h('input', { type: 'date', value: item.order_date || '' });
        const regInput = h('input', {
            type: 'number', min: '0', step: '1',
            value: String(item.registrations || 0) });
        const linePick = h('select', {},
            h('option', { value: '', selected: !item.line_type }, 'не указана'),
            (state.config.line_types || []).map((line) => h('option', {
                value: line.id, title: line.full, selected: item.line_type === line.id,
            }, line.title)));
        const note = h('textarea', { rows: '3', maxLength: CARD_LIMIT.note }, item.note || '');

        const save = h('button', { class: 'btn btn--primary', onclick: submit }, 'Сохранить');
        const dialog = openModal({
            title: 'Письмо ' + (item.incoming_no || item.case_id),
            body: [
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Входящий номер', incomingNo),
                    h('label', { class: 'field' }, 'Дата письма', incomingDate),
                    h('label', { class: 'field' }, 'Номер группы', groupInput),
                    h('label', { class: 'field' }, 'Номер указаний', orderNo),
                    h('label', { class: 'field' }, 'Дата указаний', orderDate),
                    h('label', { class: 'field' }, 'Номер ТС', tcInput),
                    h('label', { class: 'field' }, 'Дата ТС', tcDate),
                    h('label', { class: 'field' }, 'Линия связи', linePick),
                    h('label', { class: 'field' }, 'Количество регистраций', regInput),
                    h('label', { class: 'field' }, 'Срок ответа', deadline),
                    h('label', { class: 'field' }, 'Исполнитель', assignee),
                    h('label', { class: 'field' }, 'Приоритет', priority),
                    h('label', { class: 'field' }, 'Состояние', status)),
                h('label', { class: 'field' }, 'Описание', titleInput),
                h('label', { class: 'field' }, 'Примечание', note),
            ],
            footer: [
                h('span', { class: 'spacer' }),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                save,
            ],
            focus: 'input',
        });

        async function submit() {
            save.disabled = true;
            try {
                await api.patch('/api/cases/' + item.id, {
                    incoming_no: incomingNo.value.trim(),
                    incoming_date: incomingDate.value,
                    group_no: groupInput.value.trim(),
                    title: titleInput.value.trim(),
                    tc_no: tcInput.value.trim(),
                    tc_date: tcDate.value || '',
                    order_no: orderNo.value.trim(),
                    order_date: orderDate.value || '',
                    registrations: Number(regInput.value || 0),
                    line_type: linePick.value,
                    deadline: deadline.value,
                    assignee_id: assignee.value ? Number(assignee.value) : null,
                    priority: priority.value,
                    status: status.value,
                    note: note.value.trim(),
                });
                dialog.close();
                toast('Карточка письма сохранена', 'ok');
                if (after) after();
            } catch (error) {
                toastError(error);
            } finally {
                save.disabled = false;
            }
        }
    }

    /* Кому можно убрать письмо. Ошибиться при регистрации может каждый, и
       ходить за начальником из-за собственной описки человек не должен. Своё
       письмо убирает тот, кто его завёл, — но лишь пока по нему ничего не
       сделано. Как только за письмо взялись, оно перестаёт быть личной
       ошибкой и становится работой отдела. Те же правила на сервере. */
    function canDeleteCase(item) {
        if (isAdmin()) return true;
        const me = state.user || {};
        return Boolean(item.created_by && item.created_by === me.id)
            && !Number(item.reports_count || 0)
            && !item.outgoing_no;
    }

    /* Сдать по уже заведённому письму свой отчёт.

       Отдельное окно от «Сдать готовый отчёт» в списке писем: там реквизиты
       спрашивают, потому что письма может ещё не быть. Здесь письмо открыто,
       и спрашивать нечего — от повторного ввода входящего номера рождались
       письма-двойники: ошибся в знаке, и вместо новой редакции по своему
       письму заводилось второе. */
    function openUploadForCase(item, after) {
        const chosen = h('span', { class: 'small muted' }, 'файл не выбран');
        const picker = h('input', {
            type: 'file',
            accept: '.docx,.doc,.pdf,.rtf,.odt,.md,.txt',
            style: { display: 'none' },
            onchange: () => {
                const file = (picker.files || [])[0];
                chosen.textContent = file ? file.name : 'файл не выбран';
                chosen.className = file ? 'small' : 'small muted';
            },
        });
        const send = h('button', { class: 'btn btn--primary', onclick: submit },
            'Загрузить');

        const dialog = openModal({
            title: 'Свой отчёт по письму ' + (item.incoming_no || item.case_id),
            narrow: true,
            body: [
                h('div', { class: 'muted', style: { marginBottom: '10px' } },
                    'Отчёт станет новой редакцией по этому письму. Числа в нём '
                    + 'система не сверяет: его написал человек. После загрузки '
                    + 'отчёт можно отправить начальнику на проверку.'),
                h('div', { class: 'file-pick' },
                    h('button', {
                        class: 'btn btn--sm', type: 'button',
                        onclick: () => picker.click(),
                    }, iconGlyph('clip'), 'Выбрать файл'),
                    chosen, picker),
            ],
            footer: [
                h('span', { class: 'spacer' }),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                send,
            ],
        });

        async function submit() {
            const file = (picker.files || [])[0];
            if (!file) { toast('Выберите файл отчёта', 'error'); return; }
            send.disabled = true;
            const form = new FormData();
            form.append('file', file);
            // Ключ письма — его учётный номер: сервер найдёт письмо по нему
            // и заведёт редакцию, а не второе письмо.
            form.append('case_id', item.case_id);
            // На проверку отчёт уйдёт отдельной кнопкой: сперва его смотрят.
            form.append('submit', '0');
            try {
                const data = await uploadFile('/api/reports/upload', form);
                dialog.close();
                toast(data.note
                    ? 'Отчёт загружен. Текст прочитать не удалось: ' + data.note
                    : 'Отчёт загружен — посмотрите и отправьте на проверку',
                    data.note ? 'info' : 'ok', 6000);
                if (after) after();
            } catch (error) {
                toastError(error);
            } finally {
                send.disabled = false;
            }
        }
    }

    async function deleteCase(item, after) {
        const mine = !isAdmin();
        const ok = await confirmDialog({
            title: mine ? 'Убрать письмо' : 'Удалить письмо',
            message: mine
                ? 'Письмо ' + (item.incoming_no || item.case_id) + ' будет убрано '
                    + 'вместе с приложенными к нему файлами. Действие необратимо.'
                : 'Письмо ' + (item.incoming_no || item.case_id) +
                    ' будет удалено вместе со всеми версиями отчёта. Действие необратимо.',
            note: 'Сохранённые пары «черновик → правка» в обучающем наборе остаются. '
                + 'Уже выгруженные файлы DOCX лежат в каталоге выгрузок и удаляются вручную.',
            confirmText: 'Удалить',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.del('/api/cases/' + item.id);
            toast('Письмо удалено', 'ok');
            if (after) after();
        } catch (error) {
            toastError(error);
        }
    }

    // -- модальное окно «Новое письмо» ---------------------------------------

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
            group_no: '',
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
        const lines = state.config.line_types || [];

        const titleInput = h('textarea', {
            class: 'field-area', rows: 2, placeholder: 'о чём письмо',
            maxLength: CARD_LIMIT.title });
        const incomingNo = h('input', {
            type: 'text', class: 'mono', placeholder: 'ВХ-2026-0412',
            maxLength: CARD_LIMIT.incoming_no });
        const caseInput = h('input', {
            type: 'text', placeholder: 'заполнится по входящему номеру', class: 'mono',
            oninput: () => { caseTouched = true; },
        });
        let caseTouched = false;
        // Учётный номер по умолчанию повторяет входящий: два разных номера
        // руками никто вводить не станет, а поле обязательное.
        incomingNo.addEventListener('input', () => {
            if (caseTouched) return;
            caseInput.value = incomingNo.value.trim();
        });
        const incomingDate = h('input', { type: 'date', value: todayIso() });
        const groupNoInput = h('input', {
            type: 'text', placeholder: '1274',
            title: 'Откуда пришло письмо. Пишите как принято в отделе: 1274, 12/345, в/ч 74326, «2-я группа связи»',
        });
        const tcInput = h('input', {
            type: 'text', class: 'mono', placeholder: 'ТС-1274-03',
            maxLength: CARD_LIMIT.tc_no,
            title: 'Номер технического средства, о котором письмо. Ищется наравне с номерами письма',
        });
        const tcDate = h('input', { type: 'date' });
        const orderNo = h('input', {
            type: 'text', class: 'mono', placeholder: 'У-2026-14',
            maxLength: CARD_LIMIT.order_no,
            title: 'Номер указаний, по которым письмо отрабатывают',
        });
        const orderDate = h('input', { type: 'date' });
        const regInput = h('input', {
            type: 'number', min: '0', step: '1', placeholder: '0',
            title: 'Сколько регистраций числится по письму',
        });
        const deadlineInput = h('input', { type: 'date' });
        const priorityPick = h('select', {}, Object.keys(CASE_PRIORITY).map((key) =>
            h('option', { value: key }, CASE_PRIORITY[key])));
        const assigneePick = h('select', {}, h('option', { value: '' }, 'назначить позже'));
        // Список сотрудников подгружаем, не задерживая открытие окна.
        staffList().then((staff) => staff.forEach((person) => assigneePick.appendChild(
            h('option', { value: String(person.id) },
                (person.full_name || person.login) + ' — ' + (ROLE_SHORT[person.role] || person.role)))));

        // Линия связи вместо типа отчёта: отдел работает по линиям, а шаблон
        // отчёта выбирается потом, когда инженер садится за текст.
        const linePick = h('select', {},
            h('option', { value: '' }, 'не указана'),
            lines.map((item) => h('option', { value: item.id, title: item.full }, item.title)));

        // Приложенные бумаги. Файл кладётся на диск после того, как письмо
        // заведено: раньше не к чему прикладывать. Список собираем здесь и
        // отправляем по одному — так видно, какой именно файл не прошёл.
        let picked = [];
        const fileList = h('div', { class: 'file-list' });
        const fileInput = h('input', {
            type: 'file', multiple: true, style: { display: 'none' },
            onchange: (event) => {
                const chosen = Array.from(event.target.files || []);
                chosen.forEach((file) => {
                    if (!picked.some((item) => item.name === file.name && item.size === file.size)) {
                        picked.push(file);
                    }
                });
                event.target.value = '';
                drawFiles();
            },
        });

        function drawFiles() {
            clear(fileList);
            if (!picked.length) {
                fileList.appendChild(h('div', { class: 'small faint' },
                    'Скан письма, схема линии, журнал измерений — что пришло вместе с письмом'));
                return;
            }
            picked.forEach((file, index) => fileList.appendChild(
                h('div', { class: 'file-row' },
                    h('span', { class: 'file-name' }, file.name),
                    h('span', { class: 'small faint' }, fmtBytes(file.size)),
                    h('button', {
                        class: 'btn btn--sm btn--ghost',
                        title: 'Убрать из списка',
                        onclick: () => { picked.splice(index, 1); drawFiles(); },
                    }, 'Убрать'))));
        }
        drawFiles();

        const createButton = h('button', { class: 'btn btn--primary', onclick: submit },
            'Зарегистрировать');

        const dialog = openModal({
            title: 'Регистрация письма',
            body: [
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Входящий номер', incomingNo),
                    h('label', { class: 'field' }, 'Дата письма', incomingDate),
                    h('label', { class: 'field' }, 'Номер группы', groupNoInput,
                        h('span', { class: 'small faint' }, 'номер группы или части')),
                    h('label', { class: 'field' }, 'Номер указаний', orderNo),
                    h('label', { class: 'field' }, 'Дата указаний', orderDate),
                    h('label', { class: 'field' }, 'Номер ТС', tcInput,
                        h('span', { class: 'small faint' }, 'техническое средство, о котором письмо')),
                    h('label', { class: 'field' }, 'Дата ТС', tcDate),
                    h('label', { class: 'field' }, 'Линия связи', linePick),
                    h('label', { class: 'field' }, 'Количество регистраций', regInput),
                    h('label', { class: 'field' }, 'Срок ответа', deadlineInput),
                    h('label', { class: 'field' }, 'Исполнитель', assigneePick),
                    h('label', { class: 'field' }, 'Приоритет', priorityPick)),
                h('label', { class: 'field' }, 'Описание', titleInput),
                h('label', { class: 'field' }, 'Учётный номер', caseInput,
                    h('span', { class: 'small faint' },
                        'внутренний номер дела; можно повторить входящий')),
                h('div', { class: 'field' },
                    h('div', { class: 'toolbar', style: { marginBottom: '6px' } },
                        h('span', { class: 'grow' }, 'Приложенные файлы'),
                        h('button', {
                            class: 'btn btn--sm', onclick: () => fileInput.click(),
                        }, 'Выбрать файлы'),
                        fileInput),
                    fileList),
            ],
            footer: [
                h('span', { class: 'small faint spacer' },
                    'Обязательны входящий номер и описание'),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                createButton,
            ],
            focus: 'input',
        });

        async function submit() {
            const caseId = caseInput.value.trim() || incomingNo.value.trim();
            if (!caseId) {
                toast('Укажите входящий номер письма', 'error');
                incomingNo.focus();
                return;
            }
            if (!titleInput.value.trim()) {
                toast('Опишите, о чём письмо', 'error');
                titleInput.focus();
                return;
            }

            createButton.disabled = true;
            createButton.textContent = 'Создание…';
            let created = null;
            try {
                const data = await api.post('/api/cases', {
                    case_id: caseId,
                    title: titleInput.value.trim(),
                    incoming_no: incomingNo.value.trim(),
                    incoming_date: incomingDate.value || '',
                    group_no: groupNoInput.value.trim(),
                    tc_no: tcInput.value.trim(),
                    tc_date: tcDate.value || '',
                    order_no: orderNo.value.trim(),
                    order_date: orderDate.value || '',
                    registrations: Number(regInput.value || 0),
                    line_type: linePick.value,
                    deadline: deadlineInput.value || '',
                    priority: priorityPick.value,
                    assignee_id: assigneePick.value ? Number(assigneePick.value) : null,
                });
                created = data.case;
            } catch (error) {
                toastError(error);
                createButton.disabled = false;
                createButton.textContent = 'Зарегистрировать';
                return;
            }

            // Письмо заведено. Файлы кладём следом и по одному: если один
            // не прошёл, остальные всё равно на месте, а человек видит имя
            // того, который не взяли, — а не «что-то пошло не так».
            const failed = [];
            for (const file of picked) {
                try {
                    const form = new FormData();
                    form.append('file', file);
                    await uploadFile('/api/cases/' + created.id + '/files', form);
                } catch (error) {
                    failed.push(file.name + ' — ' + errorText(error));
                }
            }

            dialog.close();
            if (failed.length) {
                toast('Письмо создано, но файлы не приложены: ' + failed.join('; '), 'error');
            } else {
                toast('Письмо ' + created.case_id + ' зарегистрировано', 'ok');
            }
            location.hash = '#/case/' + created.id;
        }
    }

    // =====================================================================
    // 5а. Экран «Расход»
    // =====================================================================

    /* Расход личного состава: кто где и чем занят по дням.

       Смысл раздела в том, что человек отмечает себя сам. Расход, собранный
       через начальника, устаревает за день, и им никто не пользуется. Поэтому
       своя строка в сетке кликабельна у каждого, чужая — только у начальства.

       Раскладку периодов по суткам делает сервер (GET /api/roster): в браузере
       это пришлось бы повторить трижды — в сетке, в сводке дня и в кабинете, —
       и рано или поздно три расхода разошлись бы. */

    const ROSTER_KIND = {
        duty:     { title: 'дежурство',    short: 'Деж',  cls: 'duty' },
        work:     { title: 'работы',       short: 'Раб',  cls: 'work' },
        trip:     { title: 'командировка', short: 'Ком',  cls: 'trip' },
        study:    { title: 'учёба',        short: 'Учёб', cls: 'study' },
        vacation: { title: 'отпуск',       short: 'Отп',  cls: 'vacation' },
        sick:     { title: 'больничный',   short: 'Бол',  cls: 'sick' },
        dayoff:   { title: 'отгул',        short: 'Отг',  cls: 'dayoff' },
    };

    const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

    const rosterState = { from: '', span: 7, day: '' };

    function shiftIso(day, delta) {
        const date = new Date(day + 'T00:00:00');
        if (isNaN(date.getTime())) return day;
        date.setDate(date.getDate() + delta);
        const month = String(date.getMonth() + 1).padStart(2, '0');
        return date.getFullYear() + '-' + month + '-' + String(date.getDate()).padStart(2, '0');
    }

    /** Понедельник недели, в которую попал день: расход в отделе ведут неделями. */
    function weekStart(day) {
        const date = new Date(day + 'T00:00:00');
        if (isNaN(date.getTime())) return day;
        const shift = (date.getDay() + 6) % 7;
        return shiftIso(day, -shift);
    }

    function isWeekend(day) {
        const date = new Date(day + 'T00:00:00');
        const weekday = date.getDay();
        return weekday === 0 || weekday === 6;
    }

    async function renderRoster(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        // Окно начинается с сегодня: расход смотрят вперёд («кто где завтра»),
        // а не назад. Кнопка «Текущая неделя» ставит начало на понедельник.
        if (!rosterState.from) rosterState.from = todayIso();
        if (!rosterState.day) rosterState.day = todayIso();

        const gridBox = h('div', { class: 'card' });
        const dayBox = h('div', {});
        const rangeLabel = h('span', { class: 'small muted' });

        const spanPick = h('select', {
            title: 'Сколько дней показывать сразу',
            onchange: () => { rosterState.span = Number(spanPick.value); load(); },
        }, [7, 14].map((value) => h('option', {
            value: String(value), selected: rosterState.span === value,
        }, value + ' дней')));

        append(page, [
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' },
                    'Кто где и чем занят: дежурство, работы, выезды, отсутствия'),
                h('div', { class: 'page-head-actions' },
                    h('button', {
                        class: 'btn btn--sm', title: 'Предыдущая неделя',
                        onclick: () => { rosterState.from = shiftIso(rosterState.from, -rosterState.span); load(); },
                    }, '←'),
                    h('button', {
                        class: 'btn btn--sm',
                        onclick: () => {
                            rosterState.from = weekStart(todayIso());
                            rosterState.day = todayIso();
                            load();
                        },
                        title: 'С понедельника текущей недели',
                    }, 'Неделя'),
                    h('button', {
                        class: 'btn btn--sm',
                        title: 'Показать расход на завтра',
                        onclick: () => {
                            rosterState.from = todayIso();
                            rosterState.day = shiftIso(todayIso(), 1);
                            load();
                        },
                    }, 'Завтра'),
                    h('button', {
                        class: 'btn btn--sm', title: 'Следующая неделя',
                        onclick: () => { rosterState.from = shiftIso(rosterState.from, rosterState.span); load(); },
                    }, '→'),
                    spanPick,
                    h('button', {
                        class: 'btn btn--primary',
                        title: 'Отметить, чем вы заняты',
                        onclick: () => openRosterDialog({ user_id: (state.user || {}).id,
                            date_from: rosterState.day }, load),
                    }, 'Отметить себя'))),
            rangeLabel,
            dayBox,
            gridBox,
            rosterLegend(),
        ]);

        async function load() {
            gridBox.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' })));
            let data;
            try {
                data = await api.get('/api/roster?date_from=' + rosterState.from +
                    '&days=' + rosterState.span);
            } catch (error) {
                clear(gridBox);
                gridBox.appendChild(h('div', { class: 'empty' }, errorText(error)));
                return;
            }
            // День сводки держим внутри показанной недели: иначе человек
            // листает вперёд, а внизу висит позавчерашний расход.
            if (data.days.indexOf(rosterState.day) === -1) rosterState.day = data.days[0];
            rangeLabel.textContent = fmtDate(data.date_from) + ' — ' + fmtDate(data.date_to);
            drawGrid(data);
            await drawDay();
        }

        function drawGrid(data) {
            clear(gridBox);
            const head = h('tr', {}, h('th', { class: 'roster-name' }, 'Сотрудник'),
                data.days.map((day) => h('th', {
                    class: 'roster-day' + (day === data.today ? ' is-today' : '')
                        + (isWeekend(day) ? ' is-weekend' : '')
                        + (day === rosterState.day ? ' is-picked' : ''),
                    title: 'Показать расход на этот день',
                    onclick: () => { rosterState.day = day; load(); },
                },
                    h('b', {}, WEEKDAYS[new Date(day + 'T00:00:00').getDay()]),
                    h('span', {}, fmtDate(day).slice(0, 5)))));

            const body = h('tbody', {});
            data.staff.forEach((person) => {
                const cells = data.days.map((day) => {
                    const marks = data.cells[person.id + '|' + day] || [];
                    const mark = marks[0];
                    const classes = ['roster-cell'];
                    if (isWeekend(day)) classes.push('is-weekend');
                    if (day === data.today) classes.push('is-today');
                    if (mark) classes.push('kind-' + (ROSTER_KIND[mark.kind] || {}).cls);
                    if (person.can_edit) classes.push('is-mine');
                    return h('td', {
                        class: classes.join(' '),
                        title: mark
                            ? (mark.kind_title + (mark.place ? ' · ' + mark.place : '')
                                + (mark.note ? '\n' + mark.note : ''))
                            : (person.can_edit
                                ? 'На месте. Щёлкните, чтобы отметить другое'
                                : 'На месте'),
                        onclick: () => {
                            if (!person.can_edit) return;
                            openRosterDialog(mark || { user_id: person.id, date_from: day },
                                load);
                        },
                    }, mark
                        ? h('span', { class: 'roster-mark' },
                            h('b', {}, (ROSTER_KIND[mark.kind] || {}).short || mark.kind),
                            mark.place ? h('i', {}, mark.place) : null)
                        : (person.can_edit ? h('span', { class: 'roster-add' }, '+') : null));
                });
                // Как найти человека — подсказкой на фамилии: расход отвечает
                // «где он», и телефон тут же под рукой.
                const reach = [person.ext_no ? 'вн. ' + person.ext_no : '',
                    person.room ? 'каб. ' + person.room : '',
                    person.phone].filter(Boolean).join(' · ');
                body.appendChild(h('tr', {},
                    h('td', {
                        class: 'roster-name' + (person.is_me ? ' is-me' : ''),
                        title: reach || '',
                    },
                        h('div', {}, person.full_name),
                        h('div', { class: 'small faint' },
                            reach || ROLE_SHORT[person.role] || person.role_title)),
                    cells));
            });

            gridBox.appendChild(h('div', { class: 'table-scroll' },
                h('table', { class: 'grid grid--roster' }, h('thead', {}, head), body)));
        }

        async function drawDay() {
            clear(dayBox);
            let day;
            try {
                day = await api.get('/api/roster/day?date=' + rosterState.day);
            } catch (error) {
                dayBox.appendChild(h('div', { class: 'small muted' }, errorText(error)));
                return;
            }
            append(dayBox, [
                h('div', { class: 'stat-cards' },
                    statCard(day.present, 'на месте ' + fmtDate(day.date),
                        day.unmarked.length
                            ? 'из них без отметки: ' + day.unmarked.length
                            : 'все отметились'),
                    statCard(day.away, 'отсутствуют',
                        'выезды, отпуска, больничные, учёба'),
                    statCard(day.marked, 'отметок в расходе',
                        'из ' + day.total + ' человек в отделе')),
                h('div', { class: 'card card-pad roster-day-card' },
                    h('div', { class: 'card-title' }, 'Расход на ' + fmtDate(day.date)),
                    h('div', { class: 'roster-groups' },
                        day.groups.filter((group) => group.people.length).map((group) =>
                            h('div', { class: 'roster-group kind-' + ROSTER_KIND[group.id].cls },
                                h('div', { class: 'roster-group-head' },
                                    h('b', {}, group.title),
                                    h('span', { class: 'small faint' },
                                        String(group.people.length))),
                                group.people.map((person) => h('div', { class: 'roster-person' },
                                    h('span', {}, person.full_name),
                                    person.place
                                        ? h('i', { class: 'small muted' }, person.place) : null)))),
                        // Не отмеченные — тоже на месте, и стоят они среди
                        // своих, а не отдельной кучей «неизвестно».
                        day.unmarked.length
                            ? h('div', { class: 'roster-group kind-work' },
                                h('div', { class: 'roster-group-head' },
                                    h('b', {}, 'на месте, без отметки'),
                                    h('span', { class: 'small faint' },
                                        String(day.unmarked.length))),
                                day.unmarked.map((person) => h('div', { class: 'roster-person' },
                                    h('span', {}, person.full_name))))
                            : null),
                    day.marked === 0
                        ? h('div', { class: 'muted' },
                            'На этот день никто себя не отмечал — значит, весь '
                            + 'отдел на местах.')
                        : null),
            ]);
        }

        await load();
    }

    function rosterLegend() {
        return h('div', { class: 'roster-legend' },
            Object.keys(ROSTER_KIND).map((key) => h('span', { class: 'roster-legend-item' },
                h('i', { class: 'kind-' + ROSTER_KIND[key].cls }),
                ROSTER_KIND[key].title)),
            h('span', { class: 'small faint' },
                'Пустая клетка — человек на месте: отмечают отклонения, а не '
                + 'присутствие. Щёлкните по своей клетке, чтобы отметиться; '
                + 'по заголовку дня — чтобы увидеть расход на этот день.'));
    }

    /** Отметка в расходе: своя — у каждого, чужая — у начальника. */
    async function openRosterDialog(mark, after) {
        const existing = Boolean(mark && mark.id);
        const staff = await staffList();
        const me = state.user || {};
        const owner = existing ? mark.user_id : (mark.user_id || me.id);

        const whoPick = h('select', {}, staff.map((person) => h('option', {
            value: String(person.id), selected: person.id === owner,
        }, (person.full_name || person.login) + (person.id === me.id ? ' (я)' : ''))));
        // Чужой расход ведёт только начальство: инженеру список не нужен.
        whoPick.disabled = !isAdmin();

        const kindPick = h('select', {}, Object.keys(ROSTER_KIND).map((key) =>
            h('option', { value: key, selected: (mark.kind || 'duty') === key },
                ROSTER_KIND[key].title)));
        const fromInput = h('input', { type: 'date',
            value: mark.date_from || rosterState.day || todayIso() });
        const toInput = h('input', { type: 'date',
            value: mark.date_to || mark.date_from || rosterState.day || todayIso() });
        // Одна дата — обычный случай: отметился на завтра и забыл. Конец
        // тянется за началом, пока его не тронули руками.
        let toTouched = existing;
        fromInput.addEventListener('input', () => {
            if (toTouched || !fromInput.value) return;
            toInput.value = fromInput.value;
        });
        toInput.addEventListener('input', () => { toTouched = true; });
        const placeInput = h('input', {
            type: 'text', maxLength: 120, value: mark.place || '',
            placeholder: 'Узел 3, аппаратная 2, в/ч 74326',
            title: 'Где вы будете. Пишите как принято в отделе',
        });
        const noteInput = h('textarea', { rows: '2', maxLength: 300 }, mark.note || '');

        const save = h('button', { class: 'btn btn--primary', onclick: submit },
            existing ? 'Сохранить' : 'Отметить');

        const dialog = openModal({
            title: existing ? 'Отметка в расходе' : 'Отметить в расходе',
            body: [
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Сотрудник', whoPick),
                    h('label', { class: 'field' }, 'Чем занят', kindPick),
                    h('label', { class: 'field' }, 'С какого дня', fromInput),
                    h('label', { class: 'field' }, 'По какой день', toInput)),
                h('label', { class: 'field' }, 'Где', placeInput),
                h('label', { class: 'field' }, 'Примечание', noteInput),
            ],
            footer: [
                existing ? h('button', {
                    class: 'btn btn--danger-hover', onclick: remove,
                }, 'Убрать отметку') : null,
                h('span', { class: 'spacer' }),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                save,
            ],
            focus: 'select',
        });

        async function submit() {
            if (!fromInput.value) { toast('Укажите день', 'error'); return; }
            const payload = {
                kind: kindPick.value,
                date_from: fromInput.value,
                date_to: toInput.value || fromInput.value,
                place: placeInput.value.trim(),
                note: noteInput.value.trim(),
            };
            save.disabled = true;
            try {
                if (existing) {
                    await api.patch('/api/absences/' + mark.id, payload);
                } else {
                    payload.user_id = Number(whoPick.value) || me.id;
                    await api.post('/api/absences', payload);
                }
                dialog.close();
                toast('Расход обновлён', 'ok');
                if (after) after();
            } catch (error) {
                toastError(error);
            } finally {
                save.disabled = false;
            }
        }

        async function remove() {
            const ok = await confirmDialog({
                title: 'Убрать отметку',
                message: 'Отметка «' + (ROSTER_KIND[mark.kind] || {}).title + '» '
                    + 'с ' + fmtDate(mark.date_from) + ' по ' + fmtDate(mark.date_to)
                    + ' будет убрана из расхода.',
                confirmText: 'Убрать',
                danger: true,
            });
            if (!ok) return;
            try {
                await api.del('/api/absences/' + mark.id);
                dialog.close();
                toast('Отметка убрана', 'ok');
                if (after) after();
            } catch (error) {
                toastError(error);
            }
        }
    }

    // =====================================================================
    // 6. Экран письма: три панели
    // =====================================================================

    const wb = {
        case: null,
        coverage: {},
        reports: [],
        report: null,
        facts: null,
        files: [],
        filesError: '',
        rows: [],
        findings: [],
        factsDirty: false,
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
        wb.files = [];
        wb.filesError = '';
        wb.notes = [];
        wb.notesError = '';
        wb.rows = [];
        wb.findings = [];
        wb.factsDirty = false;
        wb.drafts = new Map();
        wb.dirty = new Set();
        wb.tab = 'sources';
        wb.activeSource = null;
        wb.focused = null;
        wb.busy = false;
        wb.coverageError = '';
        wb.nodes = {};
    }

    async function renderCase(view, caseRef) {
        resetWorkbench();
        const data = await api.get('/api/cases/' + encodeURIComponent(caseRef));
        wb.case = data.case;
        wb.coverage = data.coverage || {};
        wb.coverageError = data.coverage_error || '';
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
        // Список бумаг и примечания догружаем после отрисовки: письмо должно
        // открыться сразу, а не ждать ещё двух запросов.
        loadCaseFiles();
        loadCaseNotes();
    }

    /* Примечания к письму: переписка прямо на деле.

       Начальник пишет, что поправить, исполнитель отвечает — и всё остаётся
       при письме. Разговор у стола через неделю не вспомнить, а по бумаге,
       которую вернули на доработку, вопрос «что именно не так» возникает
       всегда. */
    async function loadCaseNotes() {
        try {
            const data = await api.get('/api/cases/' + wb.case.id + '/notes');
            wb.notes = data.notes || [];
            wb.notesError = '';
        } catch (error) {
            wb.notes = [];
            wb.notesError = errorText(error);
        }
        renderSidePanel();
    }

    async function addCaseNote(text, control) {
        if (!text.trim()) return false;
        if (control) control.disabled = true;
        try {
            await api.post('/api/cases/' + wb.case.id + '/notes', { text: text.trim() });
            await loadCaseNotes();
            return true;
        } catch (error) {
            toastError(error);
            return false;
        } finally {
            if (control) control.disabled = false;
        }
    }

    async function removeCaseNote(note) {
        const ok = await confirmDialog({
            title: 'Убрать примечание',
            message: 'Примечание будет удалено без следа.',
            confirmText: 'Убрать', danger: true,
        });
        if (!ok) return;
        try {
            await api.del('/api/cases/' + wb.case.id + '/notes/' + note.id);
            await loadCaseNotes();
        } catch (error) {
            toastError(error);
        }
    }

    function renderCaseNotes(body) {
        const notes = wb.notes || [];
        const field = h('textarea', {
            rows: 3, placeholder: 'Что поправить, о чём договорились, что уточнили',
        });
        const send = h('button', {
            class: 'btn btn--primary btn--sm',
            onclick: async () => {
                if (await addCaseNote(field.value, send)) field.value = '';
            },
        }, 'Добавить');

        if (canEdit()) {
            body.appendChild(h('div', { class: 'note-form' }, field,
                h('div', { class: 'toolbar' },
                    h('span', { class: 'small faint grow' },
                        'Увидит исполнитель и автор письма'),
                    send)));
        }

        if (!notes.length) {
            body.appendChild(h('div', { class: 'empty' },
                h('h3', {}, 'Примечаний нет'),
                h('div', {}, wb.notesError
                    || 'Здесь остаётся то, что сказали бы через стол: что '
                    + 'поправить и о чём договорились.')));
            return;
        }

        const me = state.user || {};
        notes.forEach((note) => {
            body.appendChild(h('div', { class: 'note-item' },
                h('div', { class: 'note-head' },
                    h('b', {}, note.author || 'кто-то'),
                    h('span', { class: 'small faint grow' }, fmtDateTime(note.created_at)),
                    // Своё убирает автор, чужое — начальство.
                    (note.user_id === me.id || isAdmin()) ? h('button', {
                        class: 'btn btn--icon btn--sm btn--danger-hover',
                        title: 'Убрать примечание',
                        onclick: () => removeCaseNote(note),
                    }, iconGlyph('trash')) : null),
                h('div', { class: 'note-text' }, note.text || '')));
        });
    }

    /** Загрузка редакции отчёта вместе со списком источников для правой панели. */
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
            title: 'Сохранить данные (Ctrl+S)',
            onclick: () => saveFacts(),
        }, 'Сохранить');

        const body = h('div', { class: 'panel-body' });
        const digest = h('div', { class: 'small faint' });

        wb.nodes.factsBody = body;
        wb.nodes.factsSave = saveButton;
        wb.nodes.factsDigest = digest;

        // Бумаги письма стоят над фактами: с них работа и начинается —
        // сперва читают, что пришло, и только потом заносят числа.
        const filesBox = h('div', { class: 'case-files' });
        wb.nodes.caseFiles = filesBox;
        renderCaseFiles();

        return h('section', { class: 'panel panel--facts' },
            h('div', { class: 'panel-head' },
                h('div', { class: 'panel-head-row' },
                    h('span', { class: 'panel-title' }, 'Данные для отчёта'),
                    saveButton),
                h('div', { class: 'panel-head-row' }, digest)),
            filesBox,
            body);
    }

    /* Просмотр приложенного файла, не скачивая его.

       Скан письма хочется увидеть, а не положить в «Загрузки» и открывать
       сторонней программой: половина вложений в отделе — картинки, и весь
       смысл в том, чтобы взглянуть. Браузер сам рисует картинки, PDF и
       простой текст; всё прочее просмотру не поддаётся, и по нему честно
       говорится, что открыть можно только скачав. */
    const PREVIEW_IMAGE = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff'];
    const PREVIEW_PDF = ['pdf'];
    const PREVIEW_TEXT = ['txt', 'md', 'log', 'json', 'csv'];

    function fileExt(name) {
        const dot = String(name || '').lastIndexOf('.');
        return dot === -1 ? '' : String(name).slice(dot + 1).toLowerCase();
    }

    function openFilePreview(item, href, textHref) {
        const ext = fileExt(item.name);
        const inline = href + (href.indexOf('?') === -1 ? '?' : '&') + 'inline=1';
        let view;
        if (PREVIEW_IMAGE.indexOf(ext) !== -1) {
            view = h('img', { class: 'preview-image', src: inline, alt: item.name });
        } else if (PREVIEW_PDF.indexOf(ext) !== -1 || PREVIEW_TEXT.indexOf(ext) !== -1) {
            // Встроенное окно, а не вставка текста в разметку: чужой файл в
            // своей странице — это чужой код в своей странице. Сервер отдаёт
            // его в песочнице и с запретом переугадывать тип.
            view = h('iframe', {
                class: 'preview-frame', src: inline, title: item.name,
                sandbox: '', referrerpolicy: 'no-referrer',
            });
        } else {
            view = h('div', { class: 'muted' },
                'Такой файл на экране не показать — скачайте и откройте '
                + 'своей программой.');
        }

        // Что система вычитала из файла. Показываем рядом с ним: человек
        // должен видеть, что попало в поиск, и не рассчитывать на
        // распознанное там, где его нет.
        const readBox = h('div', {});
        if (textHref && item.has_text) {
            api.get(textHref).then((data) => {
                const text = (data.text || '').trim();
                if (!text) return;
                append(readBox, [h('details', { class: 'preview-text' },
                    h('summary', {}, data.recognised
                        ? 'Что распознала система'
                        : 'Что система вычитала из файла'),
                    data.recognised ? h('div', { class: 'small muted' },
                        'Текст получен машинным распознаванием. По нему ищется '
                        + 'письмо, но числа и обозначения сверяйте с оригиналом: '
                        + 'на снимке «3,5» и «8,5» различаются одним штрихом.') : null,
                    h('pre', {}, text + (data.truncated ? '\n…' : '')))]);
            }).catch(() => { /* не прочиталось — обойдёмся картинкой */ });
        }

        const dialog = openModal({
            title: item.name,
            wide: true,
            body: [
                h('div', { class: 'preview-box' }, view),
                item.note ? h('div', { class: 'small muted' }, item.note) : null,
                item.has_text === false ? h('div', { class: 'small muted' },
                    'Текст из файла прочитать не удалось: по словам из него '
                    + 'письмо не найдётся. Сам файл на месте.') : null,
                readBox,
            ],
            footer: [
                h('span', { class: 'small faint spacer' }, fmtBytes(item.size)),
                h('a', { class: 'btn', href: href }, 'Скачать'),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Закрыть'),
            ],
        });
    }

    /* Приложенные к письму бумаги: скан письма, схема линии, журнал.
       Список отдельным запросом — он нужен и после того, как файл добавили
       или убрали, а перезагружать ради этого всё письмо расточительно. */
    async function loadCaseFiles() {
        try {
            const data = await api.get('/api/cases/' + wb.case.id + '/files');
            wb.files = data.files || [];
        } catch (error) {
            wb.files = [];
            wb.filesError = errorText(error);
        }
        renderCaseFiles();
    }

    function renderCaseFiles() {
        const box = wb.nodes.caseFiles;
        if (!box) return;
        clear(box);

        const picker = h('input', {
            type: 'file', multiple: true, style: { display: 'none' },
            onchange: async (event) => {
                const chosen = Array.from(event.target.files || []);
                event.target.value = '';
                for (const file of chosen) {
                    const form = new FormData();
                    form.append('file', file);
                    try {
                        await uploadFile('/api/cases/' + wb.case.id + '/files', form);
                    } catch (error) {
                        toast(file.name + ' — ' + errorText(error), 'error', 6000);
                    }
                }
                await loadCaseFiles();
            },
        });

        const items = wb.files || [];
        // Бумаги делим по тому, к чему они относятся: пришедшие с письмом и
        // ушедшие с ответом. В одной куче исполнитель через полгода уже не
        // отличит скан входящего от приложения к своему ответу.
        const incoming = items.filter((file) => file.stage !== 'outgoing');
        const outgoing = items.filter((file) => file.stage === 'outgoing');

        function fileRow(file) {
            return h('div', { class: 'file-row' },
                h('button', {
                    class: 'file-name file-name--link',
                    title: 'Посмотреть ' + file.name + (file.uploaded_by_name
                        ? ' · приложил ' + file.uploaded_by_name : ''),
                    onclick: () => openFilePreview(file,
                        '/api/cases/' + wb.case.id + '/files/' + file.id,
                        '/api/cases/' + wb.case.id + '/files/' + file.id + '/text'),
                }, file.name),
                h('span', { class: 'small faint nowrap' }, fmtBytes(file.size)),
                // Не прочиталось — значит, по словам из этой бумаги
                // письмо не найдётся. Человек должен это знать заранее.
                file.has_text ? null : h('span', {
                    class: 'small faint', title: 'Текст из файла прочитать не удалось: '
                        + 'по словам из него письмо не найдётся',
                }, 'без текста'),
                canEdit() && !isSent(wb.case) ? h('button', {
                    class: 'btn btn--icon btn--danger-hover',
                    title: 'Убрать файл из письма',
                    onclick: () => removeCaseFile(file),
                }, iconGlyph('trash')) : null);
        }

        append(box, [
            h('div', { class: 'toolbar' },
                h('span', { class: 'panel-title grow' }, 'Приложено к письму'),
                h('span', { class: 'small faint' }, incoming.length ? String(incoming.length) : ''),
                canEdit() && !isSent(wb.case) ? h('button', {
                    class: 'btn btn--sm', onclick: () => picker.click(),
                }, iconGlyph('clip'), 'Приложить') : null,
                picker),
            incoming.length ? h('div', { class: 'file-list' }, incoming.map(fileRow))
                : h('div', { class: 'small faint' },
                    wb.filesError || 'Ничего не приложено'),
            // Приложения к ответу показываем только когда они есть: у письма,
            // по которому ещё работают, этой стопки просто нет.
            outgoing.length ? h('div', { class: 'toolbar' },
                h('span', { class: 'panel-title grow' }, 'Ушло с ответом'),
                h('span', { class: 'small faint' }, String(outgoing.length))) : null,
            outgoing.length ? h('div', { class: 'file-list' }, outgoing.map(fileRow)) : null,
        ]);
    }

    async function removeCaseFile(file) {
        const ok = await confirmDialog({
            title: 'Убрать файл',
            message: 'Файл «' + file.name + '» будет убран из письма и удалён с диска.',
            confirmText: 'Убрать',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.del('/api/cases/' + wb.case.id + '/files/' + file.id);
            toast('Файл убран', 'ok');
        } catch (error) {
            toastError(error);
        }
        await loadCaseFiles();
    }

    /** Ответ по письму уже отправлен: исходные бумаги задним числом не правят. */
    function isSent(item) {
        return Boolean(item && item.outgoing_no);
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

    function renderFactsBody() {
        const body = wb.nodes.factsBody;
        if (!body) return;
        const scroll = body.scrollTop;
        clear(body);
        renderFactsTable(body);
        body.scrollTop = scroll;
        updateFactsDigest();
    }

    /** Блок «каких обязательных измерений не хватает» — красные ключи из coverage. */
    function buildCoverageBox() {
        // Письмо заведено под сданный файлом отчёт: факт-пакета за ним нет и
        // не нужно. Требовать измерения не за чем — по этому письму никто не
        // собирается собирать отчёт системой.
        if (wb.report && wb.report.uploaded) {
            return h('div', { class: 'coverage-box coverage-box--calm' },
                h('b', {}, 'Отчёт написан человеком'),
                h('div', { class: 'small muted' },
                    'Измерения нужны, только когда отчёт собирает система. '
                    + 'Заполните их, если решите собрать свой вариант.'));
        }
        // Факт-пакет правили руками и сломали: расчёт покрытия не прошёл.
        // Письмо при этом открывается — иначе чинить пакет было бы негде.
        if (wb.coverageError) {
            return h('div', { class: 'coverage-box coverage-box--bad' },
                h('b', {}, 'Данные не разбираются'),
                h('div', { class: 'small' }, wb.coverageError),
                h('div', { class: 'small muted' },
                    'Поправьте значения в этой панели — до этого проверка '
                    + 'обязательных не работает.'));
        }
        const coverage = localCoverage();
        if (!coverage.length) {
            return h('div', { class: 'badge badge--ok', style: { marginBottom: '12px' } },
                'Всё, что нужно отчёту, занесено');
        }
        const box = h('div', { class: 'coverage-box' },
            h('b', {}, 'Не хватает данных'),
            h('div', { class: 'small muted' },
                'Без этих значений разделы отчёта выйдут с пометкой «не хватает '
                + 'данных». Щёлкните — строка добавится в таблицу.'));
        coverage.forEach((entry) => {
            box.appendChild(h('div', { class: 'coverage-line' },
                h('span', { class: 'small' }, entry.title + ': '),
                entry.keys.map((key) => h('button', {
                    class: 'chip',
                    title: 'Добавить строку «' + factTitle(key) + '» в таблицу',
                    onclick: () => addMeasurement(key),
                }, factTitle(key), ' +'))));
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
                h('dt', {}, 'учётный номер'), h('dd', { class: 'mono' }, wb.case.case_id),
                wb.case.incoming_no ? h('dt', {}, 'входящий') : null,
                wb.case.incoming_no
                    ? h('dd', { class: 'mono' }, wb.case.incoming_no) : null,
                wb.case.line_type ? h('dt', {}, 'линия связи') : null,
                wb.case.line_type
                    ? h('dd', {}, wb.case.line_title || LINE_TITLE[wb.case.line_type]) : null,
                wb.case.tc_no ? h('dt', {}, 'номер ТС') : null,
                wb.case.tc_no ? h('dd', { class: 'mono' }, wb.case.tc_no) : null,
                h('dt', {}, 'тип отчёта'), h('dd', {}, reportTypeTitle(wb.case.report_type)),
                h('dt', {}, 'состояние'), h('dd', {}, CASE_STATUS[wb.case.status] || wb.case.status)),
            h('label', { class: 'field', style: { marginTop: '8px' } }, 'Номер группы',
                h('input', {
                    type: 'text', placeholder: '1274',
                    value: wb.facts.group_no || wb.facts.customer || '', disabled: !canEdit(),
                    title: 'Откуда пришло письмо. Пишите как принято в отделе: 1274, 12/345, в/ч 74326, «2-я группа связи»',
                    oninput: (event) => {
                        // Прежний ключ убираем, иначе в пакете останутся два
                        // номера и прочитается не тот, который вписали.
                        wb.facts.group_no = event.target.value;
                        delete wb.facts.customer;
                        markFactsDirty();
                    },
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
            : [h('th', {}, 'Что измерено'), h('th', { class: 'col-value' }, 'Значение'),
               h('th', { class: 'col-unit' }, 'Ед.'), h('th', { class: 'col-act' })];
        const table = h('table', { class: 'facts-table' + (detailed ? '' : ' is-compact') },
            h('thead', {}, h('tr', {}, ...headCells)),
            tbody);

        wb.rows.forEach((row) => tbody.appendChild(measurementRow(row, missing, detailed)));

        body.appendChild(h('div', { class: 'facts-section' },
            h('h4', {},
                'Что измерено ', h('span', { class: 'faint' }, '(' + wb.rows.length + ')'),
                h('label', { class: 'facts-toggle',
                    title: 'Показать технический ключ, способ измерения и погрешность' },
                    h('input', {
                        type: 'checkbox', checked: detailed,
                        onchange: (event) => {
                            storageSet('facts-detailed', event.target.checked ? '1' : '0');
                            renderFactsBody();
                        },
                    }), ' подробно')),
            h('div', { class: 'small faint', style: { marginBottom: '6px' } },
                'Числа, полученные при проверке. Только они попадут в отчёт: '
                + 'ничего, кроме занесённого здесь, система в текст не поставит.'),
            h('div', { class: 'table-scroll' }, table),
            canEdit() ? h('button', {
                class: 'btn btn--sm', style: { marginTop: '8px' },
                onclick: () => addMeasurement(''),
            }, '+ значение') : null));

        // -- находки
        const findingsBox = h('div', {});
        wb.findings.forEach((finding, index) => findingsBox.appendChild(findingCard(finding, index)));
        body.appendChild(h('div', { class: 'facts-section' },
            h('h4', {}, 'Что обнаружено ',
                h('span', { class: 'faint' }, '(' + wb.findings.length + ')')),
            h('div', { class: 'small faint', style: { marginBottom: '6px' } },
                'Отклонения, которые нашли при проверке: что именно, насколько '
                + 'серьёзно и чем подтверждается.'),
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
            }, '+ отклонение') : null));


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
            class: 'btn btn--icon btn--ghost', title: 'Убрать строку',
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
                h('td', {}, field('title', '', 'что измерено')),
                h('td', {}, field('value', '', 'значение')),
                h('td', {}, field('unit', '', 'ед.')),
                h('td', {}, field('method', '', 'как получено')),
                h('td', {}, field('uncertainty', '', '±')),
                remove,
            ]);
        } else {
            // Компактный режим: только название. Ключ вроде packet_count —
            // имя для кода, человеку он не говорит ничего, а места в панели
            // шириной 400 px занимает столько же, сколько название. Кому он
            // нужен — включает «подробно».
            append(tr, [
                h('td', {}, field('title', '', 'что измерено')),
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

    /* Как значение называется по-русски. Названия лежат в шаблоне рядом с
       ключами: заводят новый ключ — тут же и подписывают. Нет подписи —
       показываем сам ключ: это хуже, но честно, и видно, что подписать. */
    function factTitle(key) {
        const outline = outlineFor(wb.case && wb.case.report_type);
        const titles = (outline && outline.fact_titles) || {};
        return titles[key] || key;
    }

    function factUnit(key) {
        const outline = outlineFor(wb.case && wb.case.report_type);
        return ((outline && outline.fact_units) || {})[key] || '';
    }

    function addMeasurement(key) {
        wb.rows.push({
            key: key || '', title: key ? factTitle(key) : '', value: '',
            unit: key ? factUnit(key) : '', method: '', uncertainty: '',
            source: '', note: '',
        });
        markFactsDirty();
        renderFactsBody();
        const rows = $$('.facts-table tbody tr');
        const lastRow = rows[rows.length - 1];
        if (!lastRow) return;
        if (!key) {
            // Строку добавили вручную — начинают с названия: ключ человек
            // придумывать не должен, его подставит шаблон или он останется
            // пустым, и это законно.
            const titleInput = lastRow.querySelector('input[data-field="title"]');
            if (titleInput) titleInput.focus();
            return;
        }
        // Ключ уже известен (строку добавили из списка недостающих) — название
        // и единица подставлены шаблоном, курсор ставим сразу в «Значение».
        const value = lastRow.querySelector('input[data-field="value"]');
        if (value) value.focus();
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
        const problems = validateFacts();
        if (problems.length) {
            toast('Факт-пакет не сохранён: ' + problems.slice(0, 3).join('; ') +
                (problems.length > 3 ? ' и ещё ' + (problems.length - 3) : ''), 'error');
            return;
        }
        const facts = serializeFacts();

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

        /* У письма один отчёт. То, что раньше звалось версиями, — это его
           редакции: собрал заново или сдал файлом ещё раз. Нынешняя —
           последняя, прежние остаются на чтение как история правок:
           по ним видно, что начальник вернул и что исполнитель поправил. */
        const currentReport = wb.reports.length
            ? wb.reports[wb.reports.length - 1] : null;
        const isCurrent = !report || !currentReport || report.id === currentReport.id;
        const versionSelect = h('select', {
            title: wb.reports.length < 2
                ? 'Отчёт по письму один'
                : 'Редакции отчёта: нынешняя и прежние, на чтение',
            disabled: wb.reports.length < 2,
            onchange: (event) => switchVersion(Number(event.target.value)),
        }, wb.reports.map((item) => h('option', {
            value: item.id, selected: report && item.id === report.id,
        }, (currentReport && item.id === currentReport.id
            ? 'отчёт (редакция ' + item.version + ')'
            : 'прежняя редакция ' + item.version)
            + ' · ' + (REPORT_STATUS[item.status] || item.status))));

        /* Ответ ушёл под исходящим номером — письмо закрыто. Работать
           можно только с нынешней редакцией и только до отправки: после
           неё отчёт обязан совпадать с тем, что ушло адресату. Сервер это
           и так не даст, но человек должен видеть погашенную кнопку, а не
           ловить отказ после нажатия. */
        const sent = !!(wb.case && wb.case.outgoing_no);
        const frozen = sent || !isCurrent;

        // Со сломанным факт-пакетом генерация всё равно откажет: кнопку
        // гасим и говорим почему, а не отправляем инженера за ошибкой.
        const broken = !!wb.coverageError;
        const generateButton = h('button', {
            class: 'btn' + (report ? '' : ' btn--primary'),
            disabled: !editable || wb.busy || broken || frozen,
            title: broken
                ? 'Сначала исправьте факт-пакет: ' + wb.coverageError
                : (report
                    ? 'Написать все разделы заново новой версией (прежние сохранятся)'
                    : 'Пройти по разделам шаблона и написать черновик ответа'),
            onclick: () => generateReport(),
        }, report ? 'Переписать заново' : 'Подготовить черновик');

        const verifyButton = h('button', {
            class: 'btn', disabled: !report || wb.busy,
            title: 'Пересчитать замечания верификатора',
            onclick: () => verifyReport(),
        }, 'Проверить');

        const exportButton = h('button', {
            class: 'btn', disabled: !report || wb.busy,
            onclick: () => exportDocx(),
        }, 'Экспорт в DOCX');

        /* Отчёт, написанный руками, кладётся прямо на письмо. Раньше для
           этого приходилось возвращаться в список и заново вводить входящий
           номер: ошибся в знаке — и вместо редакции по своему письму
           заводилось второе письмо-двойник. */
        const uploadOwnButton = h('button', {
            class: 'btn' + (report ? '' : ' btn--primary'),
            disabled: !editable || wb.busy || frozen,
            title: 'Приложить к этому письму отчёт, который вы написали сами',
            onclick: () => openUploadForCase(wb.case, () => renderRoute(state.route)),
        }, 'Загрузить свой отчёт');

        const errors = report ? report.errors || 0 : 0;
        const uploaded = !!(report && report.uploaded);
        const status = report ? report.status : '';

        /* Сдать отчёт начальнику может любой сотрудник — свои отчёты в отдел
           сдают все. У сданного файлом отчёта чисел не сверяют: факт-пакета
           за ним нет, читает его человек. */
        const submitButton = h('button', {
            class: 'btn' + (status === 'draft' || status === 'rework' ? ' btn--primary' : ''),
            disabled: !report || !editable || wb.busy || frozen
                || status === 'review' || status === 'approved'
                || (!uploaded && errors > 0),
            title: (!uploaded && errors > 0)
                ? 'Сначала снимите ошибки верификатора: ' + errors
                : 'Отправить отчёт начальнику отдела на проверку',
            onclick: () => submitReport(),
        }, status === 'review' ? 'На проверке у начальника' : 'Отправить на проверку');

        /* Проверяет начальник отдела или заместитель. Остальным кнопки не
           показываем вовсе: несуществующее право не должно дразнить. */
        const approveButton = canReview() ? h('button', {
            class: 'btn btn--primary',
            disabled: !report || (!uploaded && errors > 0) || status === 'approved'
                || wb.busy || frozen,
            title: (!uploaded && errors > 0)
                ? 'Проверка заблокирована: верификатор нашёл ошибок — ' + errors
                : 'Отметить отчёт проверенным',
            onclick: () => approveReport(),
        }, status === 'approved' ? 'Проверен' : 'Отметить проверенным') : null;

        const reworkButton = canReview() ? h('button', {
            class: 'btn btn--danger-hover',
            disabled: !report || wb.busy || frozen
                || (status !== 'review' && status !== 'approved'),
            title: 'Вернуть исполнителю с замечанием',
            onclick: () => reworkReport(),
        }, 'Вернуть на исправление') : null;

        /* Последний шаг порядка отдела: ответ ушёл адресату, записываем
           исходящий номер. Отправляет исполнитель — тот же, кто готовил и
           сдавал отчёт; проверяет начальник, а отправляют все. */
        const sendButton = sent ? null : h('button', {
            class: 'btn' + (status === 'approved' ? ' btn--primary' : ''),
            disabled: !report || status !== 'approved' || wb.busy || !canEdit(),
            title: status === 'approved'
                ? 'Записать исходящий номер, под которым ушёл ответ'
                : 'Сначала начальник отдела должен отметить отчёт проверенным',
            onclick: () => sendCase(),
        }, 'Ответ отправлен');

        /* Отозвать отправку может проверяющий: запись учётная. */
        const unsendButton = (sent && canReview()) ? h('button', {
            class: 'btn btn--danger-hover', disabled: wb.busy,
            title: 'Снять запись об отправке и вернуть письмо к работе',
            onclick: () => unsendCase(),
        }, 'Отозвать отправку') : null;

        append(head, [
            h('div', { class: 'line' },
                h('div', { class: 'case-title' },
                    wb.case.title || reportTypeTitle(wb.case.report_type),
                    h('span', { class: 'case-id' },
                        wb.case.incoming_no || wb.case.case_id)),
                statusBadge(wb.case.status),
                // Срок и исполнитель — то, что спрашивают о письме первым.
                // Без них приходилось возвращаться в список.
                wb.case.deadline ? deadlineCell(wb.case) : null,
                h('span', { class: 'small muted nowrap' },
                    wb.case.assignee_name || 'исполнитель не назначен'),
                wb.case.group_no
                    ? h('span', { class: 'small muted' },
                        'группа ' + wb.case.group_no) : null,
                h('span', { class: 'spacer' }),
                canEdit() ? h('button', {
                    class: 'btn btn--sm',
                    title: 'Исполнитель, срок ответа, входящий номер, состояние',
                    onclick: () => openCaseCard(wb.case, () => renderRoute(state.route)),
                }, 'Карточка письма') : null,
                h('button', {
                    class: 'btn btn--sm',
                    title: 'Открыть разговор с помощником по этому письму',
                    onclick: (event) => askAssistantAboutCase(event.currentTarget),
                }, 'Спросить помощника'),
                canDeleteCase(wb.case) ? h('button', {
                    class: 'btn btn--sm btn--danger',
                    onclick: () => deleteCase(wb.case, () => navigate('#/cases')),
                }, isAdmin() ? 'Удалить письмо' : 'Убрать письмо') : null),
            h('div', { class: 'line' },
                wb.reports.length ? versionSelect : h('span', { class: 'small muted' }, 'отчёта ещё нет'),
                report ? reportStatusBadge(report) : null,
                // Открыта прежняя редакция: править и проверять её нельзя,
                // и человек должен видеть это, а не гадать, почему кнопки
                // не нажимаются.
                !isCurrent ? h('span', {
                    class: 'badge badge--warn',
                    title: 'Это прежняя редакция отчёта, открытая на чтение. '
                        + 'Работают с нынешней — последней в списке слева.',
                }, 'прежняя редакция') : null,
                // У сданного файлом отчёта факт-пакета нет: числа в нём не
                // сверялись ни с чем, и молчать об этом нельзя.
                uploaded ? h('span', {
                    class: 'badge badge--warn',
                    title: 'Отчёт написан вручную и загружен файлом. Числа в нём '
                        + 'система не сверяла — проверяет человек.',
                }, 'загружен файлом') : null,
                // Шапку документа при правке фактов никто не переписывает —
                // и правильно: пересборка сменила бы текст под подписью.
                // Значит, расхождение надо показать, а не прятать.
                report && report.facts_stale ? h('span', {
                    class: 'badge badge--warn',
                    title: 'Исходные данные письма правили после сборки этого '
                        + 'отчёта. В шапке документа стоят прежние сведения. '
                        + 'Чтобы обновить их — «Переписать заново».',
                }, 'собран по прежним данным') : null,
                report && !uploaded ? h('button', {
                    class: 'counter' + (errors ? ' has-errors' : ''),
                    title: 'Показать замечания',
                    onclick: () => { setTab('issues'); focusSidePanel(); },
                }, '● ошибок: ' + errors) : null,
                report && !uploaded ? h('button', {
                    class: 'counter' + (report.warnings ? ' has-warnings' : ''),
                    title: 'Показать предупреждения',
                    onclick: () => { setTab('issues'); focusSidePanel(); },
                }, '▲ предупреждений: ' + (report.warnings || 0)) : null,
                h('span', { style: { flex: '1' } }),
                uploadOwnButton,
                uploaded ? null : generateButton,
                uploaded ? null : verifyButton,
                uploaded ? h('button', {
                    class: 'btn', disabled: wb.busy,
                    title: 'Скачать файл, каким его сдали',
                    onclick: () => downloadReportFile(report),
                }, 'Скачать файл') : exportButton,
                submitButton, reworkButton, approveButton, sendButton, unsendButton),
            // Замечание проверяющего видит весь отдел, и в первую очередь
            // исполнитель: без него «требует исправления» ничего не значит.
            report && report.review_note
                ? h('div', { class: 'review-note' },
                    h('b', {}, 'Возвращено на исправление'),
                    h('div', {}, report.review_note))
                : null,
            /* Ответ ушёл, а верификатор нашёл в отчёте числа мимо
               факт-пакета. Отметку не снимаем — отзыв подписи не отзывает
               бумагу у адресата, — но молчать об этом нельзя: отдел должен
               решить, отзывать ли отправку и исправлять. */
            sent && !uploaded && errors > 0
                ? h('div', { class: 'review-note' },
                    h('b', {}, 'Ошибки в уже отправленном отчёте: ' + errors),
                    h('div', {}, 'Ответ ушёл под номером ' + wb.case.outgoing_no
                        + ', а проверка нашла в тексте числа мимо исходных данных. '
                        + 'Чтобы исправить, начальник отдела отзывает отправку. '
                        + 'Замечания — справа.'))
                : null,
            // Исходящий номер — вторая половина учёта: по нему в отделе
            // находят, чем ответили на письмо.
            sent
                ? h('div', { class: 'line small muted' },
                    h('span', {}, 'Ответ отправлен: '),
                    h('b', { class: 'mono' }, wb.case.outgoing_no),
                    wb.case.outgoing_date
                        ? h('span', {}, ' от ' + fmtDate(wb.case.outgoing_date)) : null,
                    wb.case.sent_by_name
                        ? h('span', {}, ' · ' + wb.case.sent_by_name) : null)
                : null,
        ]);
    }

    /** Значок состояния отчёта: в работе, на проверке, требует исправления. */
    function reportStatusBadge(report) {
        const tone = REPORT_STATUS_TONE[report.status] || '';
        return h('span', { class: 'badge' + (tone ? ' ' + tone : '') },
            REPORT_STATUS[report.status] || report.status);
    }

    /** Новый разговор с помощником, привязанный к текущему обращению. */
    async function askAssistantAboutCase(button) {
        if (button) button.disabled = true;
        try {
            const data = await api.post('/api/chats', {
                title: 'Обращение ' + wb.case.case_id,
                case_ref: wb.case.id,
            });
            navigate('#/chat/' + data.chat.id);
        } catch (error) {
            toastError(error);
            if (button) button.disabled = false;
        }
    }

    /* Порядок работы над письмом. Четыре шага, всегда одни и те же:
       без такой полосы инженер, открывший письмо впервые, не понимал, что
       делать раньше — заполнять измерения или жать «Сгенерировать». */
    /* Путь отчёта, который система собирает сама. */
    const CASE_STEPS = [
        { id: 'facts', title: 'Внести измерения' },
        { id: 'draft', title: 'Получить черновик' },
        { id: 'check', title: 'Проверить и поправить' },
        { id: 'review', title: 'Сдать начальнику' },
        { id: 'done', title: 'Проверено' },
        { id: 'sent', title: 'Ответ отправлен' },
    ];

    /* Путь отчёта, который написали руками и сдали файлом: шаблона и
       факт-пакета за ним нет, значит и шагов меньше. */
    const UPLOAD_STEPS = [
        { id: 'loaded', title: 'Отчёт загружен' },
        { id: 'review', title: 'Сдан на проверку' },
        { id: 'done', title: 'Проверено' },
        { id: 'sent', title: 'Ответ отправлен' },
    ];

    /** Какой шаг сейчас и что на нём делать. */
    function caseStep() {
        const report = wb.report;
        const uploaded = !!(report && report.uploaded);

        // Ответ ушёл под исходящим номером — работа по письму закончена.
        // Это последний шаг, и он важнее всех прочих признаков.
        if (wb.case && wb.case.outgoing_no) {
            return {
                id: 'sent', done: true, uploaded: uploaded,
                hint: 'Ответ отправлен под исходящим номером '
                    + wb.case.outgoing_no + '. Письмо закрыто, отчёт правке '
                    + 'не подлежит: он должен совпадать с тем, что ушло. '
                    + 'Понадобилось исправить — начальник отзывает отправку.',
            };
        }

        // Сданный файлом отчёт по шаблону не собирается: у него свой,
        // короткий путь — сдан и проверен.
        if (report && report.uploaded) {
            if (report.status === 'approved') {
                return {
                    id: 'done', uploaded: true,
                    hint: 'Отчёт проверен начальником отдела. Отправьте ответ '
                        + 'и нажмите «Ответ отправлен», чтобы записать '
                        + 'исходящий номер — иначе письмо остаётся незакрытым.',
                };
            }
            if (report.status === 'rework') {
                return {
                    id: 'review', errors: 1, uploaded: true,
                    hint: 'Начальник вернул отчёт: ' + (report.review_note || 'см. замечание')
                        + ' Исправьте документ и сдайте его заново — новой версией.',
                };
            }
            if (report.status === 'draft') {
                return {
                    id: 'loaded', uploaded: true,
                    hint: 'Отчёт загружен файлом. Посмотрите его и нажмите '
                        + '«Отправить на проверку» — до этого начальник его не '
                        + 'видит. Числа в таком отчёте система не сверяет: его '
                        + 'написал человек.',
                };
            }
            return {
                id: 'review', uploaded: true,
                hint: 'Отчёт сдан файлом и ждёт начальника отдела. Числа в нём '
                    + 'система не сверяла: факт-пакета за таким отчётом нет.',
            };
        }

        // Факт-пакет не разбирается — говорить «измерения на месте, сейчас
        // напишем черновик» нельзя: генерация всё равно откажет, а инженер
        // будет искать причину в другом месте.
        if (wb.coverageError) {
            return {
                id: 'facts', broken: true,
                hint: 'Факт-пакет не разбирается: ' + wb.coverageError
                    + ' Пока это не исправлено, черновик не построить — '
                    + 'поправьте пакет слева или в режиме JSON.',
            };
        }
        const missing = localCoverage().reduce((sum, item) => sum + item.keys.length, 0);
        if (!report) {
            return missing
                ? {
                    id: 'facts', missing: missing,
                    hint: 'Шаблон отчёта требует измерений, которых пока нет: ' + missing +
                        '. Внесите их слева — иначе разделы выйдут с пометкой '
                        + '«не хватает данных». Можно и сгенерировать как есть, '
                        + 'чтобы посмотреть структуру.',
                }
                : {
                    id: 'draft',
                    hint: 'Измерения на месте. Система пройдёт по разделам шаблона '
                        + '«' + reportTypeTitle(wb.case.report_type) + '», подберёт '
                        + 'фрагменты библиотеки и напишет черновик.',
                };
        }
        if (report.status === 'approved') {
            return {
                id: 'done',
                hint: 'Отчёт проверен начальником отдела. Выгрузите его в DOCX, '
                    + 'отправьте ответ и нажмите «Ответ отправлен», чтобы '
                    + 'записать исходящий номер — иначе письмо остаётся '
                    + 'незакрытым и висит в работе.',
            };
        }
        if (report.status === 'review') {
            return {
                id: 'review',
                hint: 'Отчёт у начальника отдела на проверке. Правка вернёт его вам: '
                    + 'начальник должен читать то, что сдали.',
            };
        }
        if (report.status === 'rework') {
            return {
                id: 'check', errors: 1,
                hint: 'Начальник вернул отчёт: ' + (report.review_note || 'см. замечание')
                    + ' Поправьте разделы и отправьте на проверку заново.',
            };
        }
        const errors = report.errors || 0;
        if (errors) {
            return {
                id: 'check', errors: errors,
                hint: 'Проверка нашла ошибок: ' + errors + '. Пока они не сняты, '
                    + 'отчёт не отправить начальнику — числа в тексте должны '
                    + 'совпадать с измерениями. Замечания справа.',
            };
        }
        return {
            id: 'review',
            hint: 'Черновик готов, ошибок нет. Прочитайте разделы, поправьте '
                + 'формулировки — и отправляйте начальнику на проверку.',
        };
    }

    function stepStrip() {
        const step = caseStep();
        const steps = step.uploaded ? UPLOAD_STEPS : CASE_STEPS;
        const index = steps.map((item) => item.id).indexOf(step.id);
        const strip = h('div', { class: 'steps' });
        steps.forEach((item, position) => {
            const state = step.done || position < index ? ' is-past'
                : position === index ? ' is-now' : '';
            strip.appendChild(h('div', { class: 'step' + state },
                h('b', {}, String(position + 1)),
                h('span', {}, item.title)));
        });
        return h('div', { class: 'step-box' + (step.errors ? ' step-box--bad' : '') },
            strip,
            h('div', { class: 'step-hint' }, step.hint));
    }

    /** Сданный файлом отчёт: показываем прочитанный текст, а не одну кнопку.

        Начальник открывает отчёт, чтобы его прочитать. Заставлять его для
        этого скачивать файл и искать, чем открыть .docx, — лишний шаг на
        каждом отчёте отдела. Текст мы извлекаем при сдаче; показываем его
        здесь и честно говорим, что это чтение файла, а подлинник — файл. */
    function uploadedReportView(report) {
        const box = h('div', { class: 'section-card' });
        box.appendChild(h('header', {},
            h('h3', {}, report.file_name || 'файл'),
            h('button', {
                class: 'btn btn--sm',
                title: 'Открыть подлинник, как его сдали',
                onclick: () => downloadReportFile(report),
            }, 'Скачать файл')));
        const text = String(report.markdown || '').trim();
        const read = h('div', { class: 'section-read' });
        if (text) {
            read.appendChild(h('div', { class: 'small muted', style: { marginBottom: '10px' } },
                'Так система прочитала сданный файл. Оформление подлинника '
                + 'здесь не передаётся — за ним «Скачать файл».'));
            read.appendChild(renderMarkdown(text));
        } else {
            read.appendChild(h('div', { class: 'faint' },
                'Прочитать текст файла не удалось — откройте подлинник кнопкой '
                + '«Скачать файл».'));
        }
        box.appendChild(read);
        return box;
    }

    function renderSections() {
        const body = wb.nodes.reportBody;
        clear(body);
        const report = wb.report;

        if (!report) {
            body.appendChild(h('div', { class: 'sections' },
                stepStrip(),
                h('div', { class: 'empty' },
                    h('h3', {}, 'Ответ ещё не готовили'),
                    h('div', { class: 'btn-row', style: { justifyContent: 'center', marginTop: '14px' } },
                        h('button', {
                            class: 'btn btn--primary',
                            disabled: !canEdit() || !!wb.coverageError,
                            title: wb.coverageError
                                ? 'Сначала исправьте факт-пакет: ' + wb.coverageError : '',
                            onclick: () => generateReport(),
                        }, 'Подготовить черновик')))));
            return;
        }

        const container = h('div', { class: 'sections' });
        container.appendChild(stepStrip());
        report.sections.forEach((section) => container.appendChild(sectionCard(section)));
        if (!report.sections.length) {
            // У сданного файлом отчёта секций и не бывает: это не сборка по
            // шаблону, а документ, который инженер написал сам.
            container.appendChild(report.uploaded
                ? uploadedReportView(report)
                : h('div', { class: 'empty' }, 'В отчёте нет секций.'));
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

        // Раздел читается заметно чаще, чем правится, а в тексте отчёта есть
        // таблицы и списки. В поле ввода они выглядят как «| Параметр | … |»,
        // поэтому по умолчанию показываем разметку, а правка — по щелчку.
        const readView = h('div', { class: 'section-read' });

        function paintRead() {
            clear(readView);
            const text = wb.drafts.has(section.section_id)
                ? wb.drafts.get(section.section_id)
                : section.text;
            if (String(text || '').trim()) {
                readView.appendChild(renderMarkdown(text));
            } else {
                readView.appendChild(h('div', { class: 'faint' }, 'раздел пуст'));
            }
            highlightCites(readView);
        }

        function setMode(editing) {
            card.classList.toggle('is-editing', editing);
            modeButton.textContent = editing ? 'Просмотр' : 'Править';
            modeButton.title = editing
                ? 'Вернуться к чтению с разметкой'
                : 'Править текст раздела';
            if (editing) {
                autosize(textarea);
                textarea.focus();
            } else {
                paintRead();
            }
        }

        const modeButton = h('button', {
            class: 'btn btn--sm btn--ghost',
            hidden: !editable,
            onclick: () => setMode(!card.classList.contains('is-editing')),
        }, 'Править');

        readView.addEventListener('click', (event) => {
            // Щелчок по ссылке на источник — это не «хочу править».
            if (!editable || event.target.closest('.cite')) return;
            setMode(true);
        });

        const card = h('article', {
            class: 'section-card', id: domId('sec-', section.section_id),
            dataset: { section: section.section_id },
        },
            h('header', {},
                h('h3', {}, h('span', { class: 'ord' }, (section.ord + 1) + '.'), ' ', section.title),
                badges,
                modeButton,
                h('button', {
                    class: 'btn btn--icon btn--ghost', title: 'Свернуть или развернуть раздел',
                    onclick: (event) => {
                        const collapsed = card.classList.toggle('is-collapsed');
                        $$('.editor-wrap, .section-read, .section-actions, .section-sources', card)
                            .forEach((node) => { node.hidden = collapsed; });
                        event.currentTarget.textContent = collapsed ? '▸' : '▾';
                    },
                }, '▾')),
            readView,
            h('div', { class: 'editor-wrap' }, backdrop, textarea),
            h('div', { class: 'section-actions' }, hintInput, regenButton, saveButton, restoreButton),
            sourceChips(section));

        paintRead();

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
        textarea.addEventListener('blur', () => { paintRead(); });

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

    /** Отметить в разметке ссылки на выбранный источник. */
    function highlightCites(root) {
        $$('.cite', root).forEach((node) => {
            node.classList.toggle('is-active', wb.activeSource === node.dataset.label);
        });
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
                message: 'Отчёт будет написан заново новой редакцией. Несохранённые правки в нынешней пропадут; прежняя редакция останется в истории.',
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
        wb.coverageError = data.coverage_error || '';
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
                message: 'Переход к другой редакции отчёта потеряет несохранённые правки (' +
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
        // Правка подписанного отчёта снимает подпись — молча этого делать
        // нельзя: инженер мог открыть раздел, чтобы просто перечитать.
        if (!(await confirmUnsign())) return;
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

    /** Согласие снять подпись, если отчёт уже утверждён. */
    async function confirmUnsign() {
        if (!wb.report || wb.report.status !== 'approved') return true;
        return confirmDialog({
            title: 'Отчёт подписан',
            message: 'Отчёт (редакция ' + wb.report.version + ') проверен. '
                + 'Правка снимет подпись: подпись стоит под тем текстом, '
                + 'который прочитал утвердивший.',
            note: 'После правки отчёт нужно будет утвердить заново.',
            confirmText: 'Править и снять подпись',
        });
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
            note: wb.report.status === 'approved'
                ? 'Отчёт утверждён — правка снимет подпись, утвердить придётся заново.' : '',
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

    /** Сдать отчёт начальнику отдела на проверку. Может любой сотрудник. */
    async function submitReport() {
        if (!wb.report) return;
        if (wb.dirty.size) {
            toast('Сначала сохраните правки разделов: ' + wb.dirty.size, 'error');
            return;
        }
        if (!wb.report.uploaded && (wb.report.errors || 0) > 0) {
            toast('Сначала снимите ошибки верификатора: ' + wb.report.errors, 'error');
            return;
        }
        const ok = await confirmDialog({
            title: 'Отправить на проверку',
            message: 'Отчёт (редакция ' + wb.report.version + ') по письму ' + wb.case.case_id
                + ' уйдёт на проверку начальнику отдела. Письмо перейдёт '
                + 'в состояние «на проверке».',
            note: 'Правка отчёта после отправки вернёт его вам: начальник должен '
                + 'читать то, что сдали.',
            confirmText: 'Отправить',
        });
        if (!ok) return;
        try {
            const data = await api.post('/api/reports/' + wb.report.id + '/submit', {});
            wb.report = normalizeReport(data.report) || wb.report;
            await reloadCase(wb.report.id);
            toast('Отчёт отправлен на проверку', 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    /** Вернуть отчёт исполнителю с замечанием. Только начальник или зам. */
    async function reworkReport() {
        if (!wb.report) return;
        const note = await promptDialog({
            multiline: true,
            title: 'Вернуть на исправление',
            message: 'Что исправить в отчёте (редакция ' + wb.report.version + ')'
                + ' по письму ' + wb.case.case_id + '?',
            note: 'Замечание увидит исполнитель и весь отдел — пишите так, '
                + 'чтобы по нему можно было работать.',
            placeholder: 'например: в выводах нет ссылки на методику измерений',
            confirmText: 'Вернуть исполнителю',
        });
        if (!note) return;
        try {
            const data = await api.post('/api/reports/' + wb.report.id + '/rework',
                { note: note });
            wb.report = normalizeReport(data.report) || wb.report;
            await reloadCase(wb.report.id);
            toast('Отчёт возвращён исполнителю', 'ok');
        } catch (error) {
            toastError(error);
        }
    }

    /** Скачать сданный файлом отчёт таким, каким его сдали. */
    function downloadReportFile(report) {
        window.open('/api/reports/' + report.id + '/file', '_blank');
    }

    /** Отметить отчёт проверенным. Только начальник отдела или заместитель. */
    async function approveReport() {
        if (!wb.report) return;
        if (!wb.report.uploaded && (wb.report.errors || 0) > 0) {
            toast('Проверка заблокирована: сначала снимите ошибки верификатора', 'error');
            return;
        }
        const ok = await confirmDialog({
            title: 'Отметить проверенным',
            message: 'Отчёт (редакция ' + wb.report.version + ') по письму ' + wb.case.case_id +
                ' будет отмечен проверенным. Письмо перейдёт в состояние '
                + '«проверен, к отправке»: дальше исполнитель отправляет ответ '
                + 'и записывает исходящий номер.',
            note: wb.report.uploaded
                ? 'Отчёт сдан файлом: числа в нём система не сверяла, вы проверяете сами.'
                : 'Разделы, которые переписал исполнитель, сохранятся парами '
                  + '«черновик модели → текст инженера»: по ним потом дообучают модель.',
            confirmText: 'Проверен',
        });
        if (!ok) return;
        try {
            const data = await withOverlay('Проверка отчёта', 'Сохраняются правки для обучающего набора.',
                () => api.post('/api/reports/' + wb.report.id + '/approve', {}));
            wb.report = normalizeReport(data.report) || wb.report;
            await reloadCase(wb.report.id);
            toast('Отчёт отмечен проверенным. Отправьте ответ и запишите '
                + 'исходящий номер', 'ok', 6000);
        } catch (error) {
            toastError(error);
        }
    }

    /** Ответ по письму ушёл адресату: записываем исходящий номер. */
    async function sendCase() {
        if (!wb.case) return;
        const number = h('input', {
            type: 'text', class: 'mono', placeholder: 'ИСХ-2026-0915',
            maxLength: CARD_LIMIT.outgoing_no,
        });
        const when = h('input', { type: 'date', value: casesState.today || todayIso() });
        const outNote = h('textarea', {
            rows: '3', maxLength: CARD_LIMIT.outgoing_note,
            placeholder: 'чем ответили, куда ушло, особые обстоятельства',
        });
        // Приложения к ответу собираем здесь же и кладём после записи номера:
        // до неё письмо ещё не отправлено, и приложениям к ответу неоткуда
        // взяться.
        let picked = [];
        const fileList = h('div', { class: 'file-list' });
        const picker = h('input', {
            type: 'file', multiple: true, style: { display: 'none' },
            onchange: (event) => {
                Array.from(event.target.files || []).forEach((file) => {
                    if (!picked.some((item) => item.name === file.name
                            && item.size === file.size)) picked.push(file);
                });
                event.target.value = '';
                drawFiles();
            },
        });

        function drawFiles() {
            clear(fileList);
            if (!picked.length) {
                fileList.appendChild(h('div', { class: 'small faint' },
                    'Что ушло вместе с ответом: сам ответ, приложения к нему'));
                return;
            }
            picked.forEach((file, index) => fileList.appendChild(
                h('div', { class: 'file-row' },
                    h('span', { class: 'file-name' }, file.name),
                    h('span', { class: 'small faint' }, fmtBytes(file.size)),
                    h('button', {
                        class: 'btn btn--sm btn--ghost',
                        onclick: () => { picked.splice(index, 1); drawFiles(); },
                    }, 'Убрать'))));
        }
        drawFiles();

        const dialog = openModal({
            title: 'Ответ отправлен',
            body: [
                h('div', { class: 'muted' },
                    'Отчёт проверен начальником отдела. Запишите, под каким '
                    + 'исходящим номером и когда ушёл ответ по письму '
                    + (wb.case.incoming_no || wb.case.case_id) + '. После этого '
                    + 'письмо считается закрытым, а отчёт — правке не подлежит.'),
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Исходящий номер', number),
                    h('label', { class: 'field' }, 'Дата отправки', when)),
                h('label', { class: 'field' }, 'Примечание к ответу', outNote),
                h('div', { class: 'field' },
                    h('div', { class: 'toolbar', style: { marginBottom: '6px' } },
                        h('span', { class: 'grow' }, 'Приложения к ответу'),
                        h('button', {
                            class: 'btn btn--sm', onclick: () => picker.click(),
                        }, 'Выбрать файлы'),
                        picker),
                    fileList),
            ],
            footer: [
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                h('button', { class: 'btn btn--primary', onclick: () => save() },
                    'Записать'),
            ],
            focus: 'input',
        });

        async function save() {
            const value = number.value.trim();
            if (!value) { toast('Укажите исходящий номер', 'error'); return; }
            let data;
            try {
                data = await api.post('/api/cases/' + wb.case.id + '/send', {
                    outgoing_no: value, outgoing_date: when.value || '',
                    outgoing_note: outNote.value.trim(),
                });
            } catch (error) {
                toastError(error);
                return;
            }
            // Приложения кладём по одному: не прошёл один — остальные на
            // месте, а человек видит имя того, который не взяли.
            const failed = [];
            for (const file of picked) {
                const form = new FormData();
                form.append('file', file);
                form.append('stage', 'outgoing');
                try {
                    await uploadFile('/api/cases/' + wb.case.id + '/files', form);
                } catch (error) {
                    failed.push(file.name + ' — ' + errorText(error));
                }
            }
            dialog.close();
            wb.case = data.case;
            refreshAll();
            loadCaseFiles();
            toast(failed.length
                ? 'Ответ записан, но приложения не легли: ' + failed.join('; ')
                : 'Ответ отправлен под номером ' + value,
                failed.length ? 'error' : 'ok', failed.length ? 8000 : 4000);
        }
    }

    /** Отозвать отправку: номер вписали не тот. Право проверяющего. */
    async function unsendCase() {
        if (!wb.case || !wb.case.outgoing_no) return;
        const ok = await confirmDialog({
            title: 'Отозвать отправку',
            message: 'Запись об отправке ответа под номером «'
                + wb.case.outgoing_no + '» будет снята, письмо вернётся '
                + 'в состояние «проверен, к отправке».',
            note: 'Отзыв виден в журнале действий. Отчёт остаётся проверенным.',
            confirmText: 'Отозвать',
            danger: true,
        });
        if (!ok) return;
        try {
            const data = await api.post('/api/cases/' + wb.case.id + '/unsend', {});
            wb.case = data.case;
            refreshAll();
            toast('Отправка отозвана', 'ok');
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
            h('button', {
                class: wb.tab === 'notes' ? 'is-active' : '',
                title: 'Переписка по письму: что поправить и о чём договорились',
                onclick: () => setTab('notes'),
            }, 'Примечания (' + (wb.notes || []).length + ')'),
        ]);

        clear(body);
        if (wb.tab === 'sources') renderSources(body, sources);
        else if (wb.tab === 'notes') renderCaseNotes(body);
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

    const libState = { docType: '', domain: '', items: [], stats: {}, chunks: 0, embeddings: 0 };

    /** Подсказка о поддерживаемых форматах и о том, чего не хватает. */
    function formatsHint() {
        const formats = state.formats;
        if (!formats) return '';
        const shown = (formats.available || []).map((item) => item.replace('.', '').toUpperCase());
        // Перечислять шесть десятков расширений бессмысленно: называем те, что
        // встречаются в библиотеке чаще всего, остальное — числом.
        const head = ['PDF', 'DOCX', 'DOC', 'XLSX', 'PPTX', 'DJVU', 'TXT'].filter(
            (item) => shown.indexOf(item) >= 0);
        const rest = shown.length - head.length;
        let text = head.join(', ');
        if (rest > 0) text += ' и ещё ' + rest + ' форматов';
        // Один формат объявляют несколько конвертеров (7z через 7z, 7za, 7zz),
        // поэтому без свёртки список повторялся: «.7z, .rar, .7z, .rar, .7z».
        const blocked = formats.blocked || [];
        const missing = [];
        blocked.forEach((spec) => (spec.suffixes || []).forEach((suffix) => {
            if (missing.indexOf(suffix) < 0) missing.push(suffix);
        }));
        if (missing.length) {
            text += '. Не читаются: ' + missing.join(', ');
        }
        return text;
    }

    async function loadFormats() {
        if (state.formats) return state.formats;
        try {
            state.formats = await api.get('/api/formats');
        } catch (error) {
            state.formats = null;
        }
        return state.formats;
    }

    /** Что система вычитала из файла — главный способ проверить качество разбора. */
    async function showDocument(item) {
        const bodyBox = h('div', {}, h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Читаем…'));
        const fileUrl = '/api/library/' + encodeURIComponent(item.doc_id) + '/file';

        const dialog = openModal({
            title: h('div', { class: 'modal-head' },
                h('b', {}, item.title || item.doc_id),
                h('span', { class: 'mono small faint' }, item.doc_id)),
            body: bodyBox,
            footer: [
                h('a', {
                    class: 'btn', href: fileUrl, target: '_blank', rel: 'noopener',
                    title: 'Открыть файл так, как он лежит в библиотеке',
                }, 'Открыть исходный файл'),
                h('a', { class: 'btn', href: fileUrl, download: '' }, 'Скачать'),
                h('button', { class: 'btn btn--primary', onclick: () => dialog.close() }, 'Закрыть'),
            ],
        });

        let data;
        try {
            data = await api.get('/api/library/' + encodeURIComponent(item.doc_id) + '/text');
        } catch (error) {
            clear(bodyBox);
            bodyBox.appendChild(h('div', { class: 'card card-pad' }, errorText(error)));
            return;
        }

        const chunks = data.chunks || [];
        const chars = (data.text || '').length;
        const warnings = (item.warnings || data.document && data.document.warnings || []);

        // Доля букв и цифр: у скана без распознавания и у PDF без карты
        // символов она проваливается, и это видно сразу.
        const meaningful = (data.text || '').replace(/\s/g, '');
        const letters = (meaningful.match(/[\p{L}\p{N}]/gu) || []).length;
        const share = meaningful.length ? letters / meaningful.length : 0;

        // Плашка показывается ТОЛЬКО когда с разбором что-то не так. Когда всё
        // в порядке, сообщать об этом нечего: число фрагментов стоит на
        // соседней вкладке, а рапорт об успехе инженер читает впустую каждый
        // раз, когда открывает документ.
        const verdict = !chars
            ? { text: 'Текст не извлёкся: скан без распознавания.', tone: 'danger' }
            : share < 0.35
                ? { text: 'Текст неразборчив: осмысленных знаков ' +
                        Math.round(share * 100) + ' %. Пересохраните или распознайте файл.',
                    tone: 'danger' }
                : chars < 400
                    ? { text: 'Текста мало: ' + chars + ' знаков. Проверьте, весь ли документ разобран.',
                        tone: 'warn' }
                    : null;

        clear(bodyBox);
        append(bodyBox, [
            verdict ? h('div', { class: 'doc-verdict doc-verdict--' + verdict.tone }, verdict.text) : null,
            !data.source_exists ? h('div', { class: 'doc-verdict doc-verdict--warn' },
                'Исходного файла нет на диске — открыть не получится.') : null,
            warnings.length ? h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'При разборе'),
                h('ul', {}, warnings.map((text) => h('li', {}, text)))) : null,
            h('div', { class: 'doc-tabs' },
                h('button', { class: 'chip is-active', onclick: (e) => switchTab(e, 'text') }, 'Текст целиком'),
                h('button', { class: 'chip', onclick: (e) => switchTab(e, 'chunks') },
                    'Фрагменты (' + chunks.length + ')')),
            h('div', { class: 'doc-pane', id: 'doc-pane-text' },
                h('pre', { class: 'doc-text' }, data.text || '')),
            h('div', { class: 'doc-pane', id: 'doc-pane-chunks', hidden: true },
                chunks.length ? chunks.map((chunk, index) => h('div', { class: 'doc-chunk' },
                    h('div', { class: 'doc-chunk-head' },
                        h('b', {}, 'Фрагмент ' + (index + 1)),
                        h('span', { class: 'faint small' },
                            (chunk.title_path || []).join(' → ') || 'без заголовка'),
                        h('span', { class: 'faint small' }, chunk.chars + ' знаков')),
                    h('div', { class: 'doc-chunk-text' }, chunk.text)))
                    : h('div', { class: 'empty' }, 'Фрагментов нет.')),
        ]);

        function switchTab(event, which) {
            $$('.doc-tabs .chip', bodyBox).forEach((chip) => chip.classList.remove('is-active'));
            event.currentTarget.classList.add('is-active');
            $('#doc-pane-text', bodyBox).hidden = which !== 'text';
            $('#doc-pane-chunks', bodyBox).hidden = which !== 'chunks';
        }
    }

    async function renderLibrary(view, focusDocId) {
        await loadFormats();
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        // Переход из панели источников помощника: показываем нужный документ,
        // сняв фильтры, иначе строки может не оказаться в таблице.
        if (focusDocId) {
            libState.docType = '';
            libState.domain = '';
        }

        const tableBox = h('div', { class: 'card' });
        const statsLine = h('div', { class: 'page-note' });
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

        const domainFilter = domainSelect({
            value: libState.domain,
            title: 'Фильтр по направлению техники',
            onchange: (value) => {
                libState.domain = value;
                loadLibrary();
            },
        });

        const forceCheckbox = h('input', { type: 'checkbox' });
        const uploadType = h('select', {}, (state.config.doc_types || []).map((type) =>
            h('option', { value: type }, docTypeLabel(type))));
        const uploadDomain = domainSelect({
            anyLabel: 'определить автоматически',
            title: 'Направление загружаемых документов',
        });

        const fileInput = h('input', {
            type: 'file', multiple: true, style: { display: 'none' },
            accept: (state.formats && state.formats.available || []).join(',') ||
                '.pdf,.docx,.md,.markdown,.txt',
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
            h('div', {}, h('b', {}, 'Перетащите файлы'), ' или нажмите для выбора'),
            h('div', { class: 'small' }, formatsHint()));

        const searchInput = h('input', {
            type: 'search', class: 'grow',
            placeholder: 'Что найти в документах — например, «занимаемая полоса частот»',
            onkeydown: (event) => {
                if (event.key === 'Enter') runSearch();
            },
        });
        const topKInput = h('input', {
            type: 'number', value: '10', min: '1', max: '50',
            title: 'Сколько фрагментов показать', style: { width: '64px' },
        });
        const searchDomain = domainSelect({ title: 'Ограничить поиск направлением' });
        const searchTypes = (state.config.doc_types || []).map((type) => {
            const checkbox = h('input', { type: 'checkbox', value: type });
            return { type: type, checkbox: checkbox, node: h('label', { class: 'inline' }, checkbox, docTypeLabel(type)) };
        });

        const focusChip = !focusDocId ? null : h('span', { class: 'badge badge--accent' },
            'показан документ ' + focusDocId,
            h('button', {
                class: 'btn btn--ghost btn--icon', title: 'Показать всю библиотеку',
                onclick: () => navigate('#/library'),
            }, '×'));

        append(page, [
            h('div', { class: 'page-head' },
                statsLine,
                // Кнопки держатся вместе: при переносе на узком экране они
                // должны уезжать на новую строку группой, а не поодиночке.
                h('div', { class: 'page-head-actions' },
                    h('button', { class: 'btn', onclick: () => loadLibrary() }, 'Обновить'),
                    canEdit() ? h('label', {
                        class: 'inline',
                        title: 'Обычно перечитываются только новые и изменившиеся файлы. ' +
                            'С этой отметкой перечитываются все — нужно после смены модели ' +
                            'встраивания или правил разбора.',
                    }, forceCheckbox, 'перечитать все файлы') : null,
                    canEdit() ? h('button', {
                        class: 'btn',
                        title: 'Прочитать каталог библиотеки и обновить поисковый индекс',
                        onclick: () => reindex(),
                    }, 'Прочитать каталог') : null)),

            canEdit() ? h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Загрузка документов'),
                h('div', { class: 'toolbar' },
                    h('label', { class: 'inline' }, 'Тип:', uploadType),
                    h('label', { class: 'inline' }, 'Направление:', uploadDomain)),
                dropzone, fileInput, uploadList) : null,

            h('div', { class: 'toolbar', style: { marginTop: '14px' } }, typeFilter, domainFilter, focusChip),
            tableBox,

            h('div', { class: 'card card-pad', style: { marginTop: '14px' } },
                h('div', { class: 'card-title' }, 'Поиск по библиотеке'),
                h('div', { class: 'toolbar' },
                    searchInput,
                    h('label', { class: 'inline' }, 'показать', topKInput, 'шт.'),
                    searchDomain,
                    h('button', { class: 'btn btn--primary', onclick: () => runSearch() }, 'Найти')),
                h('div', { class: 'toolbar small muted' }, 'типы:', searchTypes.map((item) => item.node)),
                searchResults),
        ]);

        async function loadLibrary() {
            clear(tableBox);
            tableBox.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), 'Загрузка…'));
            try {
                const query = [];
                if (libState.docType) query.push('doc_type=' + encodeURIComponent(libState.docType));
                if (libState.domain) query.push('domain=' + encodeURIComponent(libState.domain));
                const data = await api.get('/api/library' + (query.length ? '?' + query.join('&') : ''));
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
            // «Векторов 2000» при 5000 фрагментов выглядит благополучно, а на
            // деле три пятых библиотеки в смысловом поиске не участвуют. Это
            // обычный итог упавшей службы эмбеддингов посреди большой пачки,
            // и заметить его иначе нечем.
            const chunkCount = libState.chunks || totals.chunks;
            let vectors;
            if (!libState.embeddings) {
                vectors = ' · векторов нет (плотный поиск выключен)';
            } else if (chunkCount && libState.embeddings < chunkCount) {
                vectors = ' · векторов: ' + libState.embeddings + ' из ' + chunkCount +
                    ' — остальные фрагменты в смысловой поиск не попадают';
            } else {
                vectors = ' · векторов: ' + libState.embeddings;
            }
            statsLine.textContent = 'документов: ' + totals.documents +
                ' · фрагментов: ' + chunkCount + vectors;

            clear(tableBox);
            if (!libState.items.length) {
                tableBox.appendChild(h('div', { class: 'empty' },
                    h('h3', {}, 'Документов нет'),
                    h('div', {}, 'Загрузите литературу, стандарты и прошлые отчёты — они станут источниками для ссылок.')));
                return;
            }
            const body = h('tbody', {});
            libState.items.forEach((item) => {
                body.appendChild(h('tr', {
                    id: domId('doc-', item.doc_id),
                    class: focusDocId === item.doc_id ? 'is-focus' : '',
                },
                    h('td', { class: 'primary' }, h('button', {
                        class: 'linklike', title: 'Посмотреть, что система вычитала из файла',
                        onclick: () => showDocument(item),
                    }, item.title || item.doc_id)),
                    h('td', { class: 'mono small muted' }, item.doc_id),
                    h('td', { class: 'small' }, docTypeLabel(item.doc_type)),
                    h('td', {}, documentDomainCell(item)),
                    h('td', {}, documentStatusCell(item)),
                    h('td', { class: 'num' }, item.chunk_count || 0),
                    h('td', { class: 'small muted nowrap' }, fmtDateTime(item.indexed_at)),
                    h('td', { class: 'row-actions' }, isAdmin() ? h('button', {
                        class: 'btn btn--icon btn--danger-hover',
                        title: 'Убрать документ из библиотеки и из поиска',
                        onclick: () => removeDocument(item),
                    }, iconGlyph('trash')) : null)));
            });
            const libraryTable = h('table', { class: 'grid grid--library' },
                    h('thead', {}, h('tr', {},
                        h('th', {}, 'Название'), h('th', {}, 'Идентификатор'), h('th', {}, 'Тип'),
                        h('th', {}, 'Направление'),
                        h('th', {}, 'Актуальность'),
                        h('th', { class: 'num' }, 'Фрагментов'), h('th', {}, 'Прочитан'),
                        h('th', {}))),
                    body);
            tableBox.appendChild(h('div', { class: 'table-scroll' },
                makeResizable(libraryTable, 'library')));

            if (focusDocId) {
                const row = document.getElementById(domId('doc-', focusDocId));
                if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }

        /** Ячейка направления: инженеру — выпадающий список с сохранением, остальным — текст. */
        function documentDomainCell(item) {
            if (!canEdit()) {
                return h('span', { class: 'small' + (item.domain ? '' : ' faint') }, domainTitle(item.domain));
            }
            const select = domainSelect({
                value: item.domain,
                anyLabel: 'не указано',
                title: 'Направление документа',
                onchange: (value) => saveDomain(item, value, select),
            });
            select.classList.add('domain-select');
            select.classList.add('select--quiet');
            return select;
        }

        /** Актуальность документа: заменённый стандарт исчезает из поиска. */
        function documentStatusCell(item) {
            const title = item.status_title || item.status || 'действующий';
            if (!canEdit()) {
                return h('span', {
                    class: 'small' + (item.searchable === false ? ' status-off' : ''),
                }, title);
            }
            const select = h('select', {
                class: 'domain-select select--quiet' +
                    (item.searchable === false ? ' status-off' : ''),
                title: 'Актуальность документа. Заменённый и архивный не участвуют в поиске.',
                onchange: (event) => saveStatus(item, event.target.value, event.target),
            }, (state.config.statuses || DEFAULT_STATUSES).map((entry) => h('option', {
                value: entry.id, selected: (item.status || 'current') === entry.id,
            }, entry.title)));
            return select;
        }

        async function saveStatus(item, value, select) {
            const previous = item.status || 'current';
            let supersededBy = '';
            if (value === 'superseded') {
                // Своё окно, а не системное window.prompt: то выглядит как
                // сообщение браузера посреди программы и пугает.
                const answer = await promptDialog({
                    title: 'Чем заменён документ',
                    message: 'Укажите идентификатор новой редакции — он есть в столбце '
                        + '«Идентификатор». Можно оставить пустым: документ всё равно '
                        + 'будет исключён из поиска.',
                    placeholder: 'standards/obw-method-2024',
                    confirmText: 'Отметить заменённым',
                });
                if (answer === null) {           // отменили — состояние не меняем
                    select.value = previous;
                    return;
                }
                supersededBy = String(answer).trim();
            }
            select.disabled = true;
            try {
                const data = await api.put('/api/library/' + encodePath(item.doc_id) + '/status',
                    { status: value, superseded_by: supersededBy });
                Object.assign(item, data.document || {});
                select.classList.toggle('status-off', item.searchable === false);
                toast('Документ «' + (item.title || item.doc_id) + '» — ' +
                    (item.status_title || value) +
                    (item.searchable === false ? ' (исключён из поиска)' : ''), 'ok');
                // Фильтр по актуальности включён, а документ из него вышел —
                // строку надо убрать со страницы, иначе она врёт.
                if (libState.status && libState.status !== item.status) await loadLibrary();
            } catch (error) {
                select.value = previous;
                toastError(error);
            } finally {
                select.disabled = false;
            }
        }

        async function saveDomain(item, value, select) {
            const previous = item.domain || '';
            select.disabled = true;
            try {
                const data = await api.put('/api/library/' + encodePath(item.doc_id) + '/domain',
                    { domain: value });
                item.domain = (data.document && data.document.domain) || value;
                toast('Направление документа «' + (item.title || item.doc_id) + '» — ' +
                    domainTitle(item.domain), 'ok');
                if (libState.domain && libState.domain !== item.domain) await loadLibrary();
            } catch (error) {
                select.value = previous;
                toastError(error);
            } finally {
                select.disabled = false;
            }
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
                form.append('domain', uploadDomain.value);
                try {
                    const data = await uploadFile('/api/library/upload', form, (fraction) => {
                        bar.style.width = Math.round(fraction * 100) + '%';
                    });
                    bar.style.width = '100%';
                    const result = data.result || {};
                    const chunks = result.chunks !== undefined && result.chunks !== null ? result.chunks : '—';
                    status.textContent = 'готово, фрагментов: ' + chunks;
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
                    ', фрагментов ' + (result.chunks || 0), (result.failed ? 'error' : 'ok'), 9000);
                await loadLibrary();
                // Счётчик «ошибок 3» не говорит, КАКИЕ файлы не попали в
                // библиотеку и что с ними не так. Список показываем отдельно:
                // всплывающее сообщение для него слишком коротко живёт.
                showIngestReport(result);
            } catch (error) {
                toastError(error);
            }
        }

        function showIngestReport(result) {
            const failures = result.failures || [];
            const notes = result.notes || [];
            if (!failures.length && !notes.length) return;
            const block = (title, lines, level) => lines.length ? h('div', {
                style: { marginBottom: '14px' },
            },
                h('div', {},
                    h('span', { class: 'badge badge--' + level }, title),
                    h('span', { class: 'small muted', style: { marginLeft: '8px' } },
                        lines.length)),
                ...lines.map((line) => h('div', {
                    class: 'small',
                    style: { marginTop: '6px', whiteSpace: 'pre-wrap' },
                }, line))) : null;
            const dialog = openModal({
                title: 'Итог загрузки библиотеки',
                body: h('div', {},
                    block('Не принято — требует действий', failures, 'danger'),
                    block('Замечания — к сведению', notes, 'warn')),
                footer: [h('button', {
                    class: 'btn btn--primary',
                    onclick: () => dialog.close(),
                }, 'Понятно')],
            });
        }

        async function removeDocument(item) {
            const ok = await confirmDialog({
                title: 'Удалить документ',
                message: 'Документ «' + (item.title || item.doc_id) +
                    '» и все его фрагменты будут удалены из индекса.',
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
                    (types.length ? '&doc_types=' + encodeURIComponent(types.join(',')) : '') +
                    (searchDomain.value ? '&domains=' + encodeURIComponent(searchDomain.value) : '');
                const data = await api.get(url);
                clear(searchResults);
                if (data.note) searchResults.appendChild(h('div', { class: 'small muted' }, data.note));
                if (data.warning) searchResults.appendChild(h('div', { class: 'small muted' }, data.warning));
                // Половина библиотеки английская, спрашивают по-русски. Если
                // запрос дополнен по словарю — сказать об этом: иначе
                // английский текст в выдаче выглядит взявшимся ниоткуда.
                if (data.expansion && data.expansion.length) {
                    searchResults.appendChild(h('div', { class: 'small muted' },
                        'Искали также по английским терминам: ' + data.expansion.join(', ')));
                }
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
                            hit.domain ? h('span', { class: 'badge badge--info' }, domainTitle(hit.domain)) : null,
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

    // =====================================================================
    // 8а. Экран «Дашборд» — состояние отдела на сегодня
    // =====================================================================

    const boardState = { days: 30, data: null };

    async function renderBoard(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        const periodSelect = h('select', {
            onchange: (event) => {
                boardState.days = Number(event.target.value) || 30;
                load();
            },
        }, [7, 30, 90, 365].map((days) => h('option', {
            value: String(days), selected: boardState.days === days,
        }, 'за ' + days + ' ' + plural(days, 'день', 'дня', 'дней'))));

        const head = h('div', { class: 'page-head' },
            h('div', { class: 'page-note' }, 'Что в отделе происходит сегодня'),
            h('div', { class: 'page-head-actions' },
                periodSelect,
                h('button', { class: 'btn', onclick: () => load() }, 'Обновить')));

        const body = h('div', {});
        append(page, [head, body]);

        async function load() {
            clear(body);
            body.appendChild(h('div', { class: 'empty' }, h('div', { class: 'spinner' })));
            try {
                boardState.data = await api.get('/api/board?days=' + boardState.days);
                draw();
            } catch (error) {
                clear(body);
                body.appendChild(h('div', { class: 'empty' }, errorText(error)));
            }
        }

        function draw() {
            const data = boardState.data;
            const totals = data.totals || {};
            clear(body);

            /* Верхний ряд — то, что спрашивают с отдела в первую очередь. */
            const tiles = h('div', { class: 'tiles' },
                tile(totals.open || 0, 'писем в работе',
                    'всего зарегистрировано: ' + sumStatuses(data.statuses),
                    '#/cases', 'open'),
                tile(totals.overdue || 0, 'просрочено',
                    totals.overdue ? 'сроки уже прошли' : 'просроченных нет',
                    '#/cases', 'overdue', totals.overdue ? 'bad' : 'ok'),
                tile(totals.soon || 0, 'горят в ближайшие 3 дня', 'по сроку ответа',
                    '#/cases', 'open', totals.soon ? 'warn' : ''),
                tile(totals.unassigned || 0, 'без исполнителя',
                    totals.unassigned ? 'нужно распределить' : 'все письма распределены',
                    '#/cases', 'open', totals.unassigned ? 'warn' : 'ok'),
                tile(totals.staff || 0, 'человек в строю',
                    'на дежурстве: ' + (totals.on_duty || 0) + ' · отсутствуют: ' + (totals.away || 0)),
                tile((data.movement || {}).sent || 0, 'ответов отправлено',
                    'поступило за период: ' + ((data.movement || {}).came || 0) +
                    ' · проверено, но не отправлено: '
                    + Math.max(0, ((data.movement || {}).checked || 0)
                        - ((data.movement || {}).sent || 0)) +
                    ' · редакций отчётов: ' + ((data.movement || {}).reports || 0)));

            const columns = h('div', { class: 'board-cols' },
                h('div', {}, workloadCard(data), movementCard(data)),
                h('div', {}, deadlinesCard(data), dutyCard(data)));

            append(body, [tiles, columns]);
        }

        function tile(value, label, note, href, tab, kind) {
            return h('a', {
                class: 'tile' + (kind ? ' tile--' + kind : ''),
                href: href,
                onclick: () => { if (tab) casesState.view = tab; },
            },
                h('div', { class: 'tile-value' }, String(value)),
                h('div', { class: 'tile-label' }, label),
                h('div', { class: 'tile-note' }, note));
        }

        function sumStatuses(statuses) {
            return (statuses || []).reduce((sum, item) => sum + (item.count || 0), 0);
        }

        /* Нагрузка по людям: кто сколько ведёт и у кого горит. */
        function workloadCard(data) {
            const people = data.people || [];
            const card = h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Нагрузка и занятость'));
            if (!people.length) {
                card.appendChild(h('div', { class: 'empty' },
                    'Личный состав не заведён. Раздел «Сотрудники» — там заводят людей.'));
                return card;
            }
            const peak = people.reduce((max, item) => Math.max(max, item.open || 0), 0) || 1;
            const rows = h('tbody', {});
            people.forEach((person) => {
                rows.appendChild(h('tr', {},
                    h('td', {},
                        h('div', { class: person.active === false ? 'muted' : '' },
                            person.full_name),
                        h('div', { class: 'small faint' },
                            (ROLE_SHORT[person.role] || person.role) +
                            (person.team ? ' · ' + person.team : ''))),
                    h('td', {}, personState(person)),
                    h('td', { class: 'w-bar' },
                        h('div', { class: 'bar-cell' },
                            h('div', { class: 'bar' },
                                h('span', { style: { width: Math.round((person.open || 0) / peak * 100) + '%' } })),
                            h('b', {}, String(person.open || 0)))),
                    h('td', { class: 'nowrap' }, person.late
                        ? h('span', { class: 'due due--late' }, person.late + ' ' +
                            plural(person.late, 'просрочка', 'просрочки', 'просрочек'))
                        : h('span', { class: 'faint' }, '—')),
                    h('td', { class: 'nowrap small muted' },
                        person.next_deadline ? fmtDate(person.next_deadline) : '—')));
            });
            card.appendChild(h('div', { class: 'table-scroll' },
                h('table', { class: 'grid' },
                    h('thead', {}, h('tr', {},
                        h('th', {}, 'Сотрудник'),
                        h('th', {}, 'Чем занят'),
                        h('th', {}, 'Писем в работе'),
                        h('th', {}, 'Сроки'),
                        h('th', {}, 'Ближайший срок'))),
                    rows)));
            return card;
        }

        function personState(person) {
            // Отключённый сотрудник попадает в список, только пока за ним
            // числятся письма: их надо передать живому человеку. Это
            // важнее и отпуска, и дежурства — потому и первым.
            if (person.active === false) {
                return h('span', { class: 'badge badge--danger', title:
                    'Учётная запись отключена, а письма за ней числятся: '
                    + 'передайте их другому исполнителю' }, 'отключён');
            }
            if (person.away) {
                return h('span', { class: 'badge badge--warn' },
                    person.away_title + (person.away_until ? ' до ' + fmtDate(person.away_until) : ''));
            }
            if (person.on_duty) return h('span', { class: 'badge badge--accent' }, 'на дежурстве');
            if (!person.open) return h('span', { class: 'faint small' }, 'свободен');
            return h('span', { class: 'badge badge--info' }, 'в работе');
        }

        /* Сроки: что уже просрочено и что горит. */
        function deadlinesCard(data) {
            const late = data.overdue || [];
            const soon = data.soon || [];
            const card = h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Сроки'));
            if (!late.length && !soon.length) {
                card.appendChild(h('div', { class: 'empty' }, 'Просроченных и горящих писем нет.'));
                return card;
            }
            if (late.length) card.appendChild(deadlineList('Просрочено', late, 'late'));
            if (soon.length) card.appendChild(deadlineList('Ближайшие три дня', soon, 'soon'));
            return card;
        }

        function deadlineList(title, items, kind) {
            return h('div', { class: 'due-group' },
                h('div', { class: 'due-group-title' }, title),
                h('ul', { class: 'due-list' }, items.map((item) => h('li', {},
                    h('a', { href: '#/case/' + item.id }, item.title || item.case_id),
                    h('span', { class: 'due due--' + kind }, fmtDate(item.deadline)),
                    h('span', { class: 'small faint' },
                        item.assignee_name || 'не назначен')))));
        }

        /* Дежурство и отсутствия на сегодня. */
        function dutyCard(data) {
            const duty = data.duty || [];
            const absent = data.absent || [];
            const card = h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Дежурство и отсутствия'),
                h('div', { class: 'kv-line' },
                    h('b', {}, 'На дежурстве: '),
                    duty.length
                        ? duty.map((item) => h('span', { class: 'chip chip--flat' }, item.full_name))
                        : h('span', { class: 'faint' }, 'никто не назначен')));
            if (!absent.length) {
                card.appendChild(h('div', { class: 'muted small' }, 'Отсутствующих сегодня нет.'));
            } else {
                card.appendChild(h('ul', { class: 'plain-list' }, absent.map((item) =>
                    h('li', {},
                        h('b', {}, item.full_name),
                        h('span', { class: 'badge badge--warn' }, item.kind_title),
                        h('span', { class: 'small faint' },
                            'по ' + fmtDate(item.date_to))))));
            }
            if (isAdmin()) {
                card.appendChild(h('div', { class: 'btn-row', style: { marginTop: '10px' } },
                    h('button', {
                        class: 'btn btn--sm',
                        onclick: () => openAbsenceDialog(() => load()),
                    }, 'Отметить дежурство или отпуск')));
            }
            return card;
        }

        /* Движение писем по состояниям. */
        function movementCard(data) {
            const statuses = data.statuses || [];
            const total = sumStatuses(statuses) || 1;
            const card = h('div', { class: 'card card-pad' },
                h('div', { class: 'card-title' }, 'Письма по состояниям'));
            if (!statuses.length) {
                card.appendChild(h('div', { class: 'empty' }, 'Писем ещё нет.'));
                return card;
            }
            card.appendChild(h('div', { class: 'flow' }, statuses.map((item) =>
                h('div', { class: 'flow-row' },
                    h('span', { class: 'flow-name' }, item.title),
                    h('div', { class: 'bar' },
                        h('span', { style: { width: Math.round(item.count / total * 100) + '%' } })),
                    h('b', {}, String(item.count))))));
            return card;
        }

        await load();
    }

    /** Отметить дежурство, отпуск или командировку. */
    async function openAbsenceDialog(after) {
        const staff = await staffList();
        const who = h('select', {}, staff.map((person) => h('option', {
            value: String(person.id),
        }, (person.full_name || person.login) + ' — ' + (ROLE_SHORT[person.role] || person.role))));
        const kind = h('select', {}, Object.keys(ABSENCE_LABEL).map((key) =>
            h('option', { value: key }, ABSENCE_LABEL[key])));
        const from = h('input', { type: 'date', value: todayIso() });
        const to = h('input', { type: 'date', value: todayIso() });
        const note = h('input', { type: 'text', placeholder: 'необязательно' });

        const save = h('button', { class: 'btn btn--primary', onclick: submit }, 'Отметить');
        const dialog = openModal({
            title: 'Дежурство или отсутствие',
            body: [
                h('div', { class: 'form-grid' },
                    h('label', { class: 'field' }, 'Сотрудник', who),
                    h('label', { class: 'field' }, 'Вид', kind),
                    h('label', { class: 'field' }, 'С какого числа', from),
                    h('label', { class: 'field' }, 'По какое число', to)),
                h('label', { class: 'field' }, 'Примечание', note),
            ],
            footer: [
                h('span', { class: 'spacer' }),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                save,
            ],
        });

        async function submit() {
            save.disabled = true;
            try {
                await api.post('/api/absences', {
                    user_id: Number(who.value),
                    kind: kind.value,
                    date_from: from.value,
                    date_to: to.value,
                    note: note.value.trim(),
                });
                dialog.close();
                toast('Отмечено', 'ok');
                if (after) after();
            } catch (error) {
                toastError(error);
            } finally {
                save.disabled = false;
            }
        }
    }

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
            statCard(cases.total || 0, 'писем всего',
                'отправлено: ' + (cases.approved || 0) + ' · в работе: ' + (cases.draft || 0)),
            statCard(reports.total || 0, 'редакций отчётов',
                'из них проверено: ' + (reports.approved || 0)),
            statCard(fmtNumber(edits.mean_distance || 0, 3), 'средняя доля правки',
                'какую часть черновика инженер переписывает; правок в наборе: ' +
                (edits.count || 0) + ' · чем меньше, тем ближе черновик к готовому'),
            statCard(library.documents || 0, 'документов в библиотеке',
                'фрагментов: ' + (library.chunks || 0) +
                (library.embeddings ? ' · векторов: ' + library.embeddings : ' · векторов нет')));

        const bySection = (edits.by_section || []).slice()
            .sort((a, b) => (b.pairs || 0) - (a.pairs || 0));
        const maxPairs = bySection.reduce((max, item) => Math.max(max, item.pairs || 0), 0) || 1;

        const editsCard = h('div', { class: 'card card-pad' },
            h('div', { class: 'card-title' }, 'Какие разделы правят чаще всего'));

        if (!bySection.length) {
            editsCard.appendChild(h('div', { class: 'empty' },
                'Правок ещё нет: они появляются, когда инженер меняет черновик модели '
                + ', а начальник отмечает отчёт проверенным. По ним видно, какие '
                + 'разделы модель пишет хуже всего.'));
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
                        h('th', {}, 'Тип'), h('th', {}, 'Документов'), h('th', {}, 'Фрагментов'))),
                    body)));
        }

        append(page, [
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' },
                    'Объём работы, качество черновиков и состояние библиотеки'),
                h('div', { class: 'page-head-actions' },
                    h('button', {
                        class: 'btn', onclick: () => renderRoute(state.route),
                    }, 'Обновить'))),
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
    // 9. Разметка ответа помощника (упрощённый Markdown)
    // =====================================================================

    /* Полноценный парсер тут не нужен и вреден: модель пишет заголовки, списки,
       таблицы, код и выделение — этого набора достаточно. Внешних библиотек в
       контуре нет, поэтому преобразование своё, а весь текст перед вставкой
       экранируется. */

    const MD_BULLET = /^\s*[-*•]\s+(.*)$/;
    const MD_ORDERED = /^\s*\d+[.)]\s+(.*)$/;
    const MD_HEADING = /^(#{1,6})\s+(.*)$/;
    const MD_QUOTE = /^\s*>\s?(.*)$/;
    const MD_RULE = /^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/;
    const MD_FENCE = /^\s*```/;

    function isTableRow(line) {
        return /^\s*\|.*\|\s*$/.test(line);
    }

    function isTableSeparator(line) {
        return isTableRow(line) && /^[\s|:-]+$/.test(line) && line.indexOf('-') !== -1;
    }

    function tableCells(line) {
        return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
    }

    function startsBlock(line) {
        return !line.trim() || MD_HEADING.test(line) || MD_BULLET.test(line) ||
            MD_ORDERED.test(line) || MD_QUOTE.test(line) || MD_RULE.test(line) ||
            MD_FENCE.test(line) || isTableRow(line);
    }

    /** Строчное оформление: код, жирный, курсив и ссылки [S1] на источники. */
    function mdInline(text) {
        // Экранированные знаки прячем до разбора и возвращаем после: «\*» —
        // это звёздочка из данных, а не начало курсива.
        const hidden = [];
        let out = escapeHtml(String(text).replace(/\\([\\`*_[\]])/g, (whole, ch) => {
            hidden.push(ch);
            return '\u0000' + (hidden.length - 1) + '\u0000';
        }));
        out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
        out = out.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
        out = out.replace(/(^|[\s([«—])\*([^*\n]+)\*/g, '$1<i>$2</i>');
        out = out.replace(/(^|[\s([«—])_([^_\n]+)_/g, '$1<i>$2</i>');
        out = out.replace(/\[(S\d+)\]/g,
            '<button type="button" class="cite" data-label="$1" ' +
            'title="Показать источник">[$1]</button>');
        return out.replace(/\u0000(\d+)\u0000/g,
            (whole, index) => escapeHtml(hidden[Number(index)]));
    }

    /** Текст ответа → узел с разметкой. */
    function renderMarkdown(text) {
        const root = h('div', { class: 'md' });
        const lines = String(text === null || text === undefined ? '' : text)
            .replace(/\r\n/g, '\n').split('\n');
        let index = 0;

        while (index < lines.length) {
            const line = lines[index];

            if (!line.trim()) {
                index += 1;
                continue;
            }

            if (MD_FENCE.test(line)) {
                const code = [];
                index += 1;
                while (index < lines.length && !MD_FENCE.test(lines[index])) {
                    code.push(lines[index]);
                    index += 1;
                }
                index += 1;
                root.appendChild(h('pre', {}, h('code', {}, code.join('\n'))));
                continue;
            }

            if (MD_RULE.test(line)) {
                root.appendChild(h('hr', {}));
                index += 1;
                continue;
            }

            const heading = MD_HEADING.exec(line);
            if (heading) {
                const level = Math.min(heading[1].length, 4) + 2;
                root.appendChild(h('h' + level, { class: 'md-h', html: mdInline(heading[2]) }));
                index += 1;
                continue;
            }

            if (isTableRow(line)) {
                const rows = [];
                while (index < lines.length && isTableRow(lines[index])) {
                    rows.push(lines[index]);
                    index += 1;
                }
                root.appendChild(mdTable(rows));
                continue;
            }

            if (MD_QUOTE.test(line)) {
                const quote = [];
                while (index < lines.length && MD_QUOTE.test(lines[index])) {
                    quote.push(MD_QUOTE.exec(lines[index])[1]);
                    index += 1;
                }
                root.appendChild(h('blockquote', { html: mdInline(quote.join(' ')) }));
                continue;
            }

            if (MD_BULLET.test(line) || MD_ORDERED.test(line)) {
                const ordered = !MD_BULLET.test(line);
                const list = h(ordered ? 'ol' : 'ul', {});
                while (index < lines.length) {
                    const item = (ordered ? MD_ORDERED : MD_BULLET).exec(lines[index]);
                    if (!item) break;
                    list.appendChild(h('li', { html: mdInline(item[1]) }));
                    index += 1;
                }
                root.appendChild(list);
                continue;
            }

            const paragraph = [];
            while (index < lines.length && !startsBlock(lines[index])) {
                paragraph.push(lines[index].trim());
                index += 1;
            }
            root.appendChild(h('p', { html: paragraph.map(mdInline).join('<br>') }));
        }

        return root;
    }

    function mdTable(rows) {
        const head = [];
        let body = rows;
        if (rows.length > 1 && isTableSeparator(rows[1])) {
            head.push(rows[0]);
            body = rows.slice(2);
        }
        const table = h('table', { class: 'md-table' });
        if (head.length) {
            table.appendChild(h('thead', {}, h('tr', {},
                tableCells(head[0]).map((cell) => h('th', { html: mdInline(cell) })))));
        }
        table.appendChild(h('tbody', {}, body.filter((row) => !isTableSeparator(row)).map((row) =>
            h('tr', {}, tableCells(row).map((cell) => h('td', { html: mdInline(cell) }))))));
        return h('div', { class: 'md-table-scroll' }, table);
    }

    // =====================================================================
    // 10. Экран «Помощник»: разговоры | переписка | источники
    // =====================================================================

    const chat = {
        chats: [],
        archived: false,
        query: '',
        current: null,
        messages: [],
        caseInfo: null,
        sourcesOf: null,
        pendingSources: null,
        pendingExpansion: null,
        pendingWarning: null,
        /* Приложенные к следующему вопросу файлы. */
        attachments: [],
        /* Идущее создание разговора: чтобы две загрузки не завели два. */
        creating: null,
        activeLabel: null,
        /* Идёт ли ответ в ОТКРЫТОМ разговоре. Признак экранный: сам поток
           живёт при chat.live и переключением разговора не прерывается. */
        streaming: false,
        nodes: {},
        /* Ответ, который печатается прямо сейчас. Живёт отдельно от узлов
           страницы: инженер может уйти в «Письма» и вернуться — генерация
           продолжится, а не начнётся заново и не пропадёт.
           { chatId, question, answer, sources, expansion, warning } */
        live: null,
    };

    /* Где инженер был в разделе помощника и что не дописал. Хранится в
       браузере: переход на другую вкладку и обратно раньше открывал пустой
       экран «Новый разговор», хотя разговор никуда не девался. */
    const CHAT_LAST_KEY = 'rg-chat-last';
    const CHAT_DRAFT_KEY = 'rg-chat-draft';

    function rememberChat(chatId) {
        storageSet(CHAT_LAST_KEY, chatId ? String(chatId) : '');
    }

    function lastChatId() {
        const value = storageGet(CHAT_LAST_KEY, '');
        return value ? value : null;
    }

    function drafts() {
        try {
            return JSON.parse(storageGet(CHAT_DRAFT_KEY, '{}')) || {};
        } catch (error) {
            return {};
        }
    }

    function saveDraft(chatId, text) {
        const all = drafts();
        const key = String(chatId || 'new');
        if (text) all[key] = text;
        else delete all[key];
        storageSet(CHAT_DRAFT_KEY, JSON.stringify(all));
    }

    function loadDraft(chatId) {
        return drafts()[String(chatId || 'new')] || '';
    }

    /** Очистить экран разговора. Живой поток ответа при этом не трогаем. */
    function resetChat() {
        chat.current = null;
        chat.messages = [];
        chat.caseInfo = null;
        chat.sourcesOf = null;
        chat.pendingSources = null;
        chat.pendingExpansion = null;
        chat.pendingWarning = null;
        chat.attachments = [];
        chat.sentAttachments = [];
        chat.activeLabel = null;
        chat.nodes = {};
    }

    /** Уйти с экрана помощника, не обрывая ответ.
     *
     * Раньше маршрутизатор звал stopStreaming(): переход в «Письма» посреди
     * ответа убивал генерацию, и минута работы модели пропадала. Теперь
     * отпускаем только узлы страницы — поток продолжает писать в chat.live,
     * а вернувшись в раздел, инженер видит ответ дописанным.
     */
    function detachChat() {
        chat.nodes = {};
    }

    /** Оборвать поток ответа в открытом разговоре: кнопка «Стоп». */
    function stopStreaming() {
        const live = chat.live;
        if (live && live.controller) {
            try {
                live.controller.abort();
            } catch (error) {
                /* поток уже закрыт */
            }
        }
        chat.streaming = false;
    }

    /** Привести кнопки «Спросить»/«Стоп» в соответствие с тем, что происходит.
     *
     * Считаем по самому ответу, а не по общему признаку раздела: ответ мог
     * остаться работать с прошлого захода, а мог идти и в другом разговоре.
     */
    function syncStreaming() {
        setStreaming(!!(liveIsHere() && chat.live.running));
    }

    async function renderChat(view, chatId) {
        resetChat();

        const data = await api.get('/api/chats?archived=' + (chat.archived ? 'true' : 'false'));
        chat.chats = data.items || [];

        // Пришли в раздел без номера разговора — открываем тот, где были.
        // Проверяем по списку: разговор мог быть удалён или заархивирован.
        if (!chatId) {
            const remembered = lastChatId();
            const known = chat.chats.some((item) => String(item.id) === String(remembered));
            if (remembered && known) {
                replaceHash('#/chat/' + remembered);
                chatId = remembered;
            }
        }

        if (chatId) {
            try {
                const payload = await api.get('/api/chats/' + encodeURIComponent(chatId));
                chat.current = payload.chat;
                chat.messages = payload.messages || [];
                // Файлы, приложенные, но ещё не отправленные с вопросом:
                // инженер приложил дамп, отвлёкся, вернулся — всё на месте.
                const all = payload.attachments || [];
                chat.attachments = all.filter((item) => !item.message_id);
                chat.sentAttachments = all.filter((item) => item.message_id);
                const answers = chat.messages.filter((item) => item.role === 'assistant');
                chat.sourcesOf = answers.length ? answers[answers.length - 1] : null;
            } catch (error) {
                if (error instanceof ApiError && error.status === 404) {
                    // Разговор удалён — забываем его, иначе будем возвращаться
                    // к нему при каждом заходе в раздел.
                    rememberChat(null);
                    toast('Разговор не найден: возможно, он удалён', 'error');
                    navigate('#/chat');
                    return;
                }
                throw error;
            }
            rememberChat(chat.current.id);
            // Открыли архивный разговор из адресной строки — показываем архив,
            // иначе его не видно в списке слева.
            if (chat.current.archived !== chat.archived) {
                chat.archived = chat.current.archived;
                const again = await api.get('/api/chats?archived=' + (chat.archived ? 'true' : 'false'));
                chat.chats = again.items || [];
            }
        }

        clear(view);
        view.appendChild(buildChatScreen());
        renderChatList();
        renderTalkHead();
        renderFeed();
        renderChatSources();
        renderAttachments();
        loadChatCase();
        // Кнопки «Спросить»/«Стоп» приводим в соответствие потоку: он мог
        // остаться работать с прошлого захода в раздел.
        syncStreaming();
        const input = chat.nodes.input;
        if (input) {
            input.value = loadDraft(chat.current ? chat.current.id : null);
            growComposer(input);
            input.focus();
        }
    }

    /** Идёт ли прямо сейчас ответ в открытом разговоре. */
    function liveIsHere() {
        return !!(chat.live && chat.current &&
            String(chat.live.chatId) === String(chat.current.id));
    }

    function buildChatScreen() {
        const screen = h('div', {
            style: { flex: '1', display: 'flex', flexDirection: 'column', minHeight: '0' },
        });
        const bench = h('div', { class: 'chatbench', dataset: { panel: 'talk' } });
        const switcher = h('div', { class: 'panel-switch' },
            ['list', 'talk', 'side'].map((key) => h('button', {
                class: 'btn btn--sm' + (key === 'talk' ? ' btn--primary' : ''),
                dataset: { panel: key },
                onclick: () => focusChatPanel(key),
            }, { list: 'Разговоры', talk: 'Переписка', side: 'Источники' }[key])));

        chat.nodes.bench = bench;
        chat.nodes.switcher = switcher;
        append(bench, [buildChatListPanel(), buildTalkPanel(), buildChatSidePanel()]);
        append(screen, [switcher, bench]);
        return screen;
    }

    function focusChatPanel(name) {
        const bench = chat.nodes.bench;
        if (!bench) return;
        bench.dataset.panel = name;
        $$('button', chat.nodes.switcher).forEach((button) => {
            button.classList.toggle('btn--primary', button.dataset.panel === name);
        });
    }

    // -- левая панель: список разговоров -------------------------------------

    function buildChatListPanel() {
        const list = h('div', { class: 'panel-body chat-list' });
        chat.nodes.list = list;

        const search = h('input', {
            type: 'search', class: 'grow', placeholder: 'Поиск по названию',
            value: chat.query,
            oninput: debounce((event) => {
                chat.query = event.target.value.trim().toLowerCase();
                renderChatList();
            }, 150),
        });

        const activeTab = h('button', {
            class: 'btn btn--sm' + (chat.archived ? '' : ' btn--primary'),
            onclick: () => switchArchived(false),
        }, 'Активные');
        const archiveTab = h('button', {
            class: 'btn btn--sm' + (chat.archived ? ' btn--primary' : ''),
            onclick: () => switchArchived(true),
        }, 'Архив');
        chat.nodes.activeTab = activeTab;
        chat.nodes.archiveTab = archiveTab;

        const head = h('div', { class: 'panel-head' },
            h('div', { class: 'panel-head-row' },
                h('span', { class: 'panel-title' }, 'Разговоры'),
                h('button', {
                    class: 'btn btn--sm btn--primary',
                    title: 'Начать новый разговор',
                    onclick: () => createChat(),
                }, '+ Новый разговор')),
            h('div', { class: 'panel-head-row' }, search),
            h('div', { class: 'panel-head-row' }, activeTab, archiveTab));

        return h('section', { class: 'panel panel--chatlist' }, head, list);
    }

    async function switchArchived(flag) {
        if (chat.archived === flag) return;
        chat.archived = flag;
        chat.nodes.activeTab.classList.toggle('btn--primary', !flag);
        chat.nodes.archiveTab.classList.toggle('btn--primary', flag);
        await reloadChatList();
    }

    async function reloadChatList() {
        try {
            const data = await api.get('/api/chats?archived=' + (chat.archived ? 'true' : 'false'));
            chat.chats = data.items || [];
            renderChatList();
        } catch (error) {
            toastError(error);
        }
    }

    function renderChatList() {
        const box = chat.nodes.list;
        if (!box) return;
        clear(box);
        const items = !chat.query ? chat.chats : chat.chats.filter((item) =>
            String(item.title || '').toLowerCase().indexOf(chat.query) !== -1);

        if (!items.length) {
            box.appendChild(h('div', { class: 'empty small' },
                chat.chats.length ? 'Ничего не найдено.'
                    : (chat.archived ? 'В архиве пусто.'
                        : 'Разговоров пока нет.')));
            return;
        }
        items.forEach((item) => box.appendChild(chatListItem(item)));
    }

    function chatListItem(item) {
        const isCurrent = !!chat.current && chat.current.id === item.id;
        const title = h('div', { class: 'title', title: 'Двойной клик — переименовать' }, item.title);
        // Переход откладываем на четверть секунды: иначе первый щелчок двойного
        // клика успевает перерисовать список, и переименование не начинается.
        let pending = null;
        const node = h('div', {
            class: 'chat-item' + (isCurrent ? ' is-active' : ''),
            onclick: (event) => {
                if (event.target.closest('button') || event.target.tagName === 'INPUT') return;
                clearTimeout(pending);
                if (isCurrent) return;
                pending = setTimeout(() => navigate('#/chat/' + item.id), 220);
            },
            ondblclick: (event) => {
                if (event.target.tagName === 'INPUT') return;
                clearTimeout(pending);
                startRename(node, item, title);
            },
        },
            title,
            h('div', { class: 'meta' },
                item.domain ? h('span', { class: 'badge badge--info' }, domainTitle(item.domain)) : null,
                item.case_ref ? h('span', { class: 'badge badge--accent' }, 'письмо') : null,
                h('span', { class: 'faint' }, item.message_count + ' ' +
                    plural(item.message_count, 'сообщение', 'сообщения', 'сообщений')),
                h('span', { class: 'faint' }, fmtDateTime(item.updated_at))),
            h('div', { class: 'chat-item-actions' },
                h('button', {
                    class: 'btn btn--ghost btn--icon',
                    title: item.archived ? 'Вернуть из архива' : 'Убрать в архив',
                    onclick: () => toggleArchive(item),
                }, item.archived ? '↥' : '↧'),
                h('button', {
                    class: 'btn btn--ghost btn--icon', title: 'Удалить разговор',
                    onclick: () => removeChat(item),
                }, '×')));
        return node;
    }

    /** Переименование по двойному клику: поле прямо в строке списка. */
    function startRename(node, item, titleNode) {
        if ($('input.rename', node)) return;
        const input = h('input', {
            type: 'text', class: 'rename', value: item.title, maxLength: CARD_LIMIT.title });
        node.replaceChild(input, titleNode);
        input.focus();
        input.select();

        let closed = false;
        const finish = async (save) => {
            if (closed) return;
            closed = true;
            const value = input.value.trim();
            if (input.parentNode === node) node.replaceChild(titleNode, input);
            if (!save || !value || value === item.title) return;
            try {
                const data = await api.patch('/api/chats/' + item.id, { title: value });
                item.title = data.chat.title;
                titleNode.textContent = item.title;
                if (chat.current && chat.current.id === item.id) {
                    chat.current.title = item.title;
                    renderTalkHead();
                }
            } catch (error) {
                toastError(error);
            }
        };

        input.addEventListener('keydown', (event) => {
            event.stopPropagation();
            if (event.key === 'Enter') {
                event.preventDefault();
                finish(true);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                finish(false);
            }
        });
        input.addEventListener('blur', () => finish(true));
    }

    function renameChatDialog(item) {
        const input = h('input', { type: 'text', class: 'grow', value: item.title });
        const save = async () => {
            const value = input.value.trim();
            dialog.close();
            if (!value || value === item.title) return;
            try {
                const data = await api.patch('/api/chats/' + item.id, { title: value });
                item.title = data.chat.title;
                if (chat.current && chat.current.id === item.id) chat.current.title = item.title;
                upsertChatInList(item);
                renderTalkHead();
            } catch (error) {
                toastError(error);
            }
        };
        const dialog = openModal({
            narrow: true,
            title: 'Название разговора',
            body: h('label', { class: 'field' }, 'Название', input),
            footer: [
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                h('button', { class: 'btn btn--primary', onclick: save }, 'Сохранить'),
            ],
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') save();
        });
        setTimeout(() => input.focus(), 30);
    }

    async function createChat() {
        try {
            const data = await api.post('/api/chats', {});
            if (chat.archived) chat.archived = false;
            navigate('#/chat/' + data.chat.id);
        } catch (error) {
            toastError(error);
        }
    }

    async function toggleArchive(item) {
        try {
            const data = await api.patch('/api/chats/' + item.id, { archived: !item.archived });
            item.archived = data.chat.archived;
            if (chat.current && chat.current.id === item.id) {
                chat.current = data.chat;
                renderTalkHead();
            }
            toast(item.archived ? 'Разговор убран в архив' : 'Разговор возвращён из архива', 'ok', 3000);
            await reloadChatList();
        } catch (error) {
            toastError(error);
        }
    }

    async function removeChat(item) {
        const ok = await confirmDialog({
            title: 'Удалить разговор',
            message: 'Разговор «' + item.title + '» будет удалён вместе со всеми сообщениями. ' +
                'Действие необратимо.',
            confirmText: 'Удалить',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.del('/api/chats/' + item.id);
            toast('Разговор удалён', 'ok');
            if (chat.current && chat.current.id === item.id) {
                navigate('#/chat');
                return;
            }
            await reloadChatList();
        } catch (error) {
            toastError(error);
        }
    }

    function upsertChatInList(item) {
        if (!item) return;
        const index = chat.chats.findIndex((row) => row.id === item.id);
        if (item.archived !== chat.archived) {
            if (index !== -1) chat.chats.splice(index, 1);
        } else if (index === -1) {
            chat.chats.unshift(item);
        } else {
            chat.chats[index] = item;
        }
        renderChatList();
    }

    // -- центральная панель: переписка ---------------------------------------

    function buildTalkPanel() {
        const head = h('div', { class: 'panel-head' });
        const feed = h('div', { class: 'panel-body chat-feed' });
        chat.nodes.talkHead = head;
        chat.nodes.feed = feed;
        return h('section', { class: 'panel panel--talk' }, head, feed, buildComposer());
    }

    function renderTalkHead() {
        const head = chat.nodes.talkHead;
        if (!head) return;
        clear(head);
        const current = chat.current;
        const count = chat.messages.length;

        append(head, [
            h('div', { class: 'panel-head-row' },
                h('div', { class: 'chat-title', title: current ? current.title : '' },
                    current ? current.title : 'Новый разговор'),
                current && current.archived ? h('span', { class: 'badge' }, 'в архиве') : null,
                h('span', { style: { flex: '1' } }),
                current ? h('button', {
                    class: 'btn btn--sm', onclick: () => renameChatDialog(current),
                }, 'Переименовать') : null,
                current ? h('button', {
                    class: 'btn btn--sm', onclick: () => toggleArchive(current),
                }, current.archived ? 'Из архива' : 'В архив') : null,
                current ? h('button', {
                    class: 'btn btn--sm btn--danger', onclick: () => removeChat(current),
                }, 'Удалить') : null),
            h('div', { class: 'panel-head-row small muted' },
                h('span', {}, count + ' ' + plural(count, 'сообщение', 'сообщения', 'сообщений')),
                current && current.domain
                    ? h('span', { class: 'badge badge--info' }, domainTitle(current.domain))
                    : h('span', { class: 'faint' }, 'поиск по всем направлениям'),
                current ? h('span', { class: 'faint' }, 'изменён ' + fmtDateTime(current.updated_at)) : null),
        ]);
    }

    function renderFeed() {
        const feed = chat.nodes.feed;
        if (!feed) return;
        clear(feed);
        const live = liveIsHere() ? chat.live : null;
        if (!chat.messages.length && !live) {
            feed.appendChild(emptyChatState());
            return;
        }
        chat.messages.forEach((message) => feed.appendChild(messageNode(message)));
        if (live) feed.appendChild(liveNode(live));
        scrollFeed();
    }

    /** Пузырь недописанного ответа. Пересобирается при каждом возвращении
     *  в раздел, поэтому узлы держим в chat.live, а не в замыкании. */
    function liveNode(live) {
        const box = h('div', {});
        // Вопрос сохраняется на сервере сразу, ещё до первого куска ответа.
        // Вернувшись в раздел, мы уже получили его в списке сообщений —
        // второй раз рисовать не надо, иначе вопрос двоится на экране.
        const already = chat.messages.some(
            (item) => live.questionId && String(item.id) === String(live.questionId));
        if (!already) {
            box.appendChild(messageNode({
                id: live.questionId || 0, role: 'user', content: live.question,
                sources: [], created_at: live.askedAt, attachments: live.attachments,
            }));
        }
        const body = h('div', { class: 'body' + (chat.streaming ? ' is-typing' : '') });
        const note = h('span', { class: 'faint' }, chat.streaming ? 'печатает…' : '');
        box.appendChild(h('div', { class: 'msg msg--assistant' },
            h('div', { class: 'who' }, 'Помощник', note), body));
        live.body = body;
        live.note = note;
        if (live.answer) renderAnswer(body, live.answer, live.sources);
        return box;
    }

    function emptyChatState() {
        return h('div', { class: 'empty chat-empty' },
            h('h3', {}, 'Вопрос по библиотеке'),
            h('div', {}, 'Ответ со ссылками на документы библиотеки.'),
            h('div', { class: 'chat-examples' }, CHAT_EXAMPLES.map((item) => h('button', {
                class: 'example', title: 'Подставить вопрос в поле ввода',
                onclick: () => useExample(item),
            },
                h('span', { class: 'badge badge--info' }, domainTitle(item.domain)),
                h('span', { class: 'text' }, item.text)))));
    }

    function useExample(item) {
        const input = chat.nodes.input;
        if (!input) return;
        input.value = item.text;
        growComposer(input);
        input.focus();
    }

    function messageNode(message) {
        const isUser = message.role === 'user';
        const body = h('div', { class: 'body' });
        if (isUser) body.textContent = message.content;
        else renderAnswer(body, message.content, message.sources);

        const sources = message.sources || [];
        const who = isUser
            ? (state.user ? (state.user.full_name || state.user.login) : 'Инженер')
            : 'Помощник';

        // Приложенные файлы показываем под вопросом: через неделю иначе
        // не понять, откуда в ответе взялись эти числа.
        const files = attachmentsOf(message);

        return h('div', {
            class: 'msg msg--' + (isUser ? 'user' : 'assistant'),
            dataset: { id: String(message.id || '') },
        },
            h('div', { class: 'who' }, who,
                h('span', { class: 'faint' }, fmtDateTime(message.created_at))),
            body,
            files.length ? h('div', { class: 'msg-files' }, files.map((item) =>
                h('span', { class: 'attach attach--sent', title: item.note || '' },
                    iconGlyph('clip'), h('b', {}, item.name)))) : null,
            !isUser && message.meta && message.meta.interrupted
                ? h('div', { class: 'msg-note' },
                    'Ответ прерван: инженер закрыл вкладку или нажал «Стоп». '
                    + 'Спросите ещё раз, если нужен полный разбор.') : null,
            // Строку со счётчиком показываем и когда источников нет вовсе.
            // Раньше она в этом случае пропадала — а это самый важный
            // случай: ответ написан по памяти модели, и сказать об этом
            // нужно громче всего, а не тише.
            !isUser && (sources.length || (message.meta && message.meta.found !== undefined))
                ? h('div', { class: 'msg-foot' },
                sources.length ? h('button', {
                    class: 'btn btn--sm btn--ghost',
                    onclick: () => showSources(message),
                }, 'источники: ' + sources.length) : null,
                message.meta && message.meta.found !== undefined && !message.meta.cited
                    ? h('span', { class: 'badge badge--warn' },
                        'ответ не опирается на библиотеку') : null,
                message.meta && message.meta.found !== undefined
                    ? h('span', { class: 'small faint' },
                        'найдено фрагментов: ' + message.meta.found +
                        // Часть найденного в окно модели не поместилась.
                        // Молчать об этом нельзя: инженер решит, что в
                        // библиотеке больше ничего и нет.
                        (message.meta.shown !== undefined && message.meta.shown < message.meta.found
                            ? ' (модель видела ' + message.meta.shown + ')' : '') +
                        (message.meta.documents ? ' в ' + message.meta.documents + ' док.' : '') +
                        ', процитировано: ' + (message.meta.cited || 0))
                    : null) : null);
    }

    /** Файлы, приложенные к этому сообщению. */
    function attachmentsOf(message) {
        if (message.attachments) return message.attachments;
        if (!message.id) return [];
        return (chat.sentAttachments || []).filter(
            (item) => String(item.message_id) === String(message.id));
    }

    /** Вставить размеченный ответ и оживить ссылки [S1].
     *
     * `sources` — подборка, по которой писался этот ответ. Модель иногда
     * ссылается на [S9], когда фрагментов пять: такая кнопка раньше молча
     * не открывала ничего. Теперь она видна как несуществующая ссылка. */
    function renderAnswer(container, text, sources) {
        clear(container);
        container.appendChild(renderMarkdown(text));
        const known = new Set((sources || []).map((item) => item.label));
        $$('.cite', container).forEach((button) => {
            if (sources && !known.has(button.dataset.label)) {
                button.classList.add('cite--dead');
                button.disabled = true;
                button.title = 'Такого фрагмента в подборке нет: ссылка ошибочна';
                return;
            }
            button.classList.toggle('is-active', button.dataset.label === chat.activeLabel);
            button.addEventListener('click', () => selectSource(button.dataset.label, container));
        });
    }

    function selectSource(label, container) {
        const holder = container.closest('.msg');
        const id = holder ? Number(holder.dataset.id) : 0;
        const message = chat.messages.find((item) => item.id === id);
        if (message) chat.sourcesOf = message;
        chat.activeLabel = chat.activeLabel === label ? null : label;
        renderChatSources();
        markCites();
        if (!chat.activeLabel) return;
        focusChatPanel('side');
        const card = document.getElementById(domId('csrc-', label));
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function markCites() {
        if (!chat.nodes.feed) return;
        $$('.cite', chat.nodes.feed).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.label === chat.activeLabel);
        });
    }

    function showSources(message) {
        chat.sourcesOf = message;
        chat.pendingSources = null;
        chat.activeLabel = null;
        renderChatSources();
        markCites();
        focusChatPanel('side');
    }

    function scrollFeed() {
        const feed = chat.nodes.feed;
        if (feed) feed.scrollTop = feed.scrollHeight;
    }

    // -- поле ввода ----------------------------------------------------------

    function growComposer(node) {
        node.style.height = 'auto';
        node.style.height = Math.min(node.scrollHeight + 2, 200) + 'px';
    }

    /* Что можно приложить к вопросу. Список для окна выбора файла: без него
       проводник показывает все файлы подряд, и инженер пробует приложить
       архив, который всё равно не прочитается. */
    const ATTACH_ACCEPT = [
        '.txt', '.log', '.csv', '.json', '.xml', '.pcap', '.pcapng', '.cap', '.har',
        '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp',
        '.pdf', '.docx', '.doc', '.xlsx', '.odt', '.md',
    ].join(',');

    const ATTACH_KIND_LABEL = {
        dump: 'дамп или лог',
        image: 'снимок экрана',
        document: 'документ',
    };

    function buildAttachBar() {
        const list = h('div', { class: 'attach-list' });
        const picker = h('input', {
            type: 'file', multiple: true, accept: ATTACH_ACCEPT,
            style: { display: 'none' },
            onchange: async (event) => {
                const files = Array.prototype.slice.call(event.target.files || []);
                event.target.value = '';
                // По одному и по очереди. Разом — это две беды сразу: файлы
                // выстраивались в порядке ответа сервера, а не выбора, и в
                // пустом разговоре каждая загрузка успевала создать свой
                // чат — файлы расходились по разным разговорам.
                for (const file of files) await uploadAttachment(file);
            },
        });
        const button = h('button', {
            class: 'btn btn--sm',
            title: 'Приложить дамп, лог, снимок экрана или документ. ' +
                'Файл будет разобран и уйдёт помощнику вместе с вопросом.',
            onclick: () => picker.click(),
        }, iconGlyph('clip'), 'Приложить файл');

        chat.nodes.attachList = list;
        chat.nodes.attachButton = button;
        return h('div', { class: 'attach-bar' }, button, list, picker);
    }

    function renderAttachments() {
        const list = chat.nodes.attachList;
        if (!list) return;
        clear(list);
        (chat.attachments || []).forEach((item) => {
            const chip = h('span', {
                class: 'attach' + (item.chars ? '' : ' attach--empty'),
                title: item.note || (ATTACH_KIND_LABEL[item.kind] || item.kind) +
                    ', разобрано знаков: ' + item.chars,
            },
                h('b', {}, item.name),
                h('span', { class: 'faint' }, item.chars
                    ? fmtNumber(item.chars, 0) + ' зн.'
                    : 'текста нет'),
                h('button', {
                    class: 'attach-drop', title: 'Убрать', onclick: () => dropAttachment(item),
                }, '×'));
            list.appendChild(chip);
        });
    }

    async function ensureChat() {
        if (chat.current) return chat.current;
        // Второй вызов, пока идёт первый, ждёт его, а не заводит свой чат.
        if (chat.creating) return chat.creating;
        chat.creating = _createChat().finally(() => { chat.creating = null; });
        return chat.creating;
    }

    async function _createChat() {
        const created = await api.post('/api/chats', {
            domain: chat.nodes.domain ? chat.nodes.domain.value : '',
        });
        chat.current = created.chat;
        upsertChatInList(chat.current);
        renderTalkHead();
        rememberChat(chat.current.id);
        replaceHash('#/chat/' + chat.current.id);
        return chat.current;
    }

    async function uploadAttachment(file) {
        const button = chat.nodes.attachButton;
        if (button) button.disabled = true;
        try {
            await ensureChat();
            const form = new FormData();
            form.append('file', file);
            const data = await uploadFile(
                '/api/chats/' + chat.current.id + '/attachments', form);
            chat.attachments = (chat.attachments || []).concat([data.attachment]);
            renderAttachments();
            if (!data.attachment.chars) {
                toast('Файл «' + data.attachment.name + '» приложен, но текста в нём нет: ' +
                    (data.attachment.note || 'формат не читается'), 'error', 6000);
            }
        } catch (error) {
            toastError(error);
        } finally {
            if (button) button.disabled = false;
        }
    }

    async function dropAttachment(item) {
        try {
            await api.del('/api/chats/' + chat.current.id + '/attachments/' + item.id);
            chat.attachments = (chat.attachments || []).filter((one) => one.id !== item.id);
            renderAttachments();
        } catch (error) {
            toastError(error);
        }
    }

    function buildComposer() {
        const input = h('textarea', {
            class: 'composer-input', rows: '1', spellcheck: 'false',
            placeholder: 'Вопрос по библиотеке. Enter — отправить, Shift+Enter — новая строка',
            oninput: (event) => {
                growComposer(event.target);
                // Недописанный вопрос переживает переход на другую вкладку.
                saveDraft(chat.current ? chat.current.id : null, event.target.value);
            },
            onkeydown: (event) => {
                event.stopPropagation();
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    send();
                }
            },
        });
        const sendButton = h('button', {
            class: 'btn btn--primary', onclick: () => send(),
        }, 'Спросить');
        const stopButton = h('button', {
            class: 'btn btn--danger', hidden: true,
            title: 'Прервать генерацию ответа',
            onclick: () => abortAnswer(),
        }, 'Стоп');
        const domainPick = domainSelect({
            value: chat.current ? chat.current.domain : '',
            title: 'Ограничить поиск направлением техники',
            onchange: (value) => setChatDomain(value),
        });
        const caseLine = h('span', { class: 'case-plate' });

        chat.nodes.input = input;
        chat.nodes.send = sendButton;
        chat.nodes.stop = stopButton;
        chat.nodes.domain = domainPick;
        chat.nodes.casePlate = caseLine;

        return h('div', { class: 'composer' },
            h('div', { class: 'composer-top' },
                h('span', { class: 'small muted' }, 'Искать в:'), domainPick, caseLine),
            buildAttachBar(),
            h('div', { class: 'composer-row' }, input, sendButton, stopButton));
    }

    /** Плашка с номером письма, если разговор к нему привязан. */
    async function loadChatCase() {
        const plate = chat.nodes.casePlate;
        if (!plate || !chat.current || !chat.current.case_ref) return;
        const reference = chat.current.case_ref;
        clear(plate);
        plate.appendChild(h('a', {
            class: 'badge badge--accent', href: '#/case/' + reference,
            title: 'Открыть письмо',
        }, 'обращение #' + reference));
        try {
            const data = await api.get('/api/cases/' + reference);
            chat.caseInfo = data.case;
            if (chat.current && chat.current.case_ref === reference) {
                clear(plate);
                plate.appendChild(h('a', {
                    class: 'badge badge--accent', href: '#/case/' + reference,
                    title: 'Открыть письмо: ' + (chat.caseInfo.title || ''),
                }, 'обращение ' + chat.caseInfo.case_id));
            }
        } catch (error) {
            /* письмо могло быть удалено — оставляем плашку с номером */
        }
    }

    async function setChatDomain(value) {
        if (!chat.current) return;
        try {
            const data = await api.patch('/api/chats/' + chat.current.id, { domain: value });
            chat.current = data.chat;
            upsertChatInList(chat.current);
            renderTalkHead();
            toast(value ? 'Поиск ограничен направлением: ' + domainTitle(value)
                : 'Поиск по всем направлениям', 'ok', 3000);
        } catch (error) {
            chat.nodes.domain.value = chat.current.domain || '';
            toastError(error);
        }
    }

    // -- правая панель: источники ответа -------------------------------------

    function buildChatSidePanel() {
        const body = h('div', { class: 'panel-body' });
        chat.nodes.sideBody = body;
        return h('section', { class: 'panel panel--chatside' },
            h('div', { class: 'panel-head' },
                h('div', { class: 'panel-head-row' },
                    h('span', { class: 'panel-title' }, 'Источники ответа'))),
            body);
    }

    function renderChatSources() {
        const box = chat.nodes.sideBody;
        if (!box) return;
        clear(box);
        // Половина библиотеки английская, а спрашивают по-русски. Без этой
        // строки английский фрагмент среди источников выглядит взявшимся
        // ниоткуда, и доверия к ответу не прибавляет.
        if (chat.pendingWarning) {
            box.appendChild(h('div', { class: 'small muted' }, chat.pendingWarning));
        }
        if (chat.pendingExpansion && chat.pendingExpansion.length) {
            box.appendChild(h('div', { class: 'small muted' },
                'Искали также по английским терминам: ' + chat.pendingExpansion.join(', ')));
        }
        const items = chat.pendingSources || (chat.sourcesOf ? chat.sourcesOf.sources : []) || [];
        if (!items.length) {
            box.appendChild(h('div', { class: 'empty small' },
                'Фрагменты, на которые опирается ответ.'));
            return;
        }
        // Сколько документов сверил помощник — видно сразу, до чтения ответа.
        const docs = documentsOf(items);
        if (docs.length > 1) {
            box.appendChild(h('div', { class: 'src-summary' },
                'Ответ собран по ' + docs.length + ' ' +
                plural(docs.length, 'документу', 'документам', 'документам') +
                ': ' + docs.map((item) => item.title).join('; ')));
        }
        items.forEach((source) => box.appendChild(sourceCard(source)));
    }

    /** Список документов, из которых взяты фрагменты, в порядке появления. */
    function documentsOf(items) {
        const seen = {};
        const out = [];
        (items || []).forEach((item) => {
            const key = item.doc_id || item.citation || '';
            if (!key || seen[key]) return;
            seen[key] = true;
            out.push({ doc_id: key, title: item.title || key });
        });
        return out;
    }

    function sourceCard(source) {
        const docId = String(source.chunk_uid || '').split('#')[0];
        const node = h('div', {
            id: domId('csrc-', source.label),
            class: 'source-item' + (chat.activeLabel === source.label ? ' is-active is-open' : ''),
            onclick: (event) => {
                if (event.target.closest('a')) return;
                node.classList.toggle('is-open');
            },
        },
            h('div', {},
                h('span', { class: 'label' }, '[' + source.label + ']'),
                h('span', { class: 'citation' }, source.citation || source.chunk_uid)),
            h('div', { class: 'src-meta' },
                h('span', { class: 'badge' }, docTypeLabel(source.doc_type)),
                source.domain
                    ? h('span', { class: 'badge badge--info' }, domainTitle(source.domain))
                    : h('span', { class: 'badge' }, 'направление не указано'),
                docId ? h('a', {
                    class: 'small', href: '#/library/' + encodePath(docId),
                    title: 'Показать документ в библиотеке',
                }, docId) : null),
            h('div', { class: 'quote' }, source.text || ''));
        return node;
    }

    // -- вопрос и потоковый ответ --------------------------------------------

    async function send() {
        if (chat.streaming) return;
        const input = chat.nodes.input;
        const text = input.value.trim();
        if (!text) {
            toast('Введите вопрос', 'error', 3000);
            return;
        }

        if (!chat.current) {
            try {
                // Адрес внутри меняется без перерисовки экрана: иначе
                // поток ответа оборвался бы на первом же событии.
                await ensureChat();
                saveDraft(null, '');
            } catch (error) {
                toastError(error);
                return;
            }
        }

        input.value = '';
        growComposer(input);
        saveDraft(chat.current.id, '');
        const sentWith = chat.attachments || [];
        chat.attachments = [];
        renderAttachments();
        await streamAnswer(text, sentWith);
    }

    function abortAnswer() {
        if (!chat.streaming) return;
        stopStreaming();
        toast('Генерация прервана', 'ok', 3000);
    }

    function setStreaming(flag) {
        chat.streaming = flag;
        if (chat.nodes.send) {
            chat.nodes.send.hidden = flag;
            chat.nodes.send.disabled = flag;
        }
        if (chat.nodes.stop) chat.nodes.stop.hidden = !flag;
    }

    /** Разбор одного события SSE: строки «data: {json}». */
    function parseEvent(raw) {
        const payload = raw.split('\n')
            .filter((line) => line.slice(0, 5) === 'data:')
            .map((line) => line.slice(5).trim())
            .join('');
        if (!payload) return null;
        try {
            return JSON.parse(payload);
        } catch (error) {
            return { type: 'error', error: 'сервер прислал испорченное событие' };
        }
    }

    async function streamAnswer(text, attachments) {
        const placeholder = chat.nodes.feed ? $('.chat-empty', chat.nodes.feed) : null;
        if (placeholder) placeholder.remove();

        // Всё состояние ответа живёт здесь, а не в узлах страницы: инженер
        // может уйти в другой раздел и вернуться, генерация не прервётся.
        const live = {
            chatId: chat.current.id,
            question: text,
            questionId: 0,
            askedAt: new Date().toISOString(),
            attachments: attachments || [],
            answer: '',
            body: null,
            note: null,
            running: true,
            controller: null,
            /* Подборка именно этого ответа: по ней сверяются ссылки [S1].
               Общее поле раздела принадлежит открытому разговору, а ответ
               мог идти в другом. */
            sources: [],
        };
        chat.live = live;

        chat.pendingSources = [];
        chat.pendingExpansion = null;
        chat.pendingWarning = null;
        chat.sourcesOf = null;
        chat.activeLabel = null;

        const controller = new AbortController();
        live.controller = controller;
        setStreaming(true);
        renderFeed();
        renderChatSources();

        let done = null;
        let failed = '';
        let aborted = false;
        let painted = 0;

        /* Рисуем не чаще раза в 70 мс и только если узлы на месте: пока
           инженер в другом разделе, текст просто копится в live.answer. */
        const paint = (force) => {
            const now = Date.now();
            if (!force && now - painted < 70) return;
            painted = now;
            if (!live.body || !live.body.isConnected) return;
            renderAnswer(live.body, live.answer, live.sources);
            live.body.classList.add('is-typing');
            scrollFeed();
        };

        try {
            const response = await fetch('/api/chats/' + live.chatId + '/stream', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                body: JSON.stringify({ text: text }),
                signal: controller.signal,
            });
            if (response.status === 401) {
                goToLogin();
                return;
            }
            if (!response.ok || !response.body) {
                let message = 'ошибка сервера (код ' + response.status + ')';
                try {
                    const data = await response.json();
                    if (data && data.error) message = data.error;
                } catch (error) {
                    /* тело не JSON — оставляем сообщение по коду */
                }
                throw new ApiError(response.status, message);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            while (true) {
                const piece = await reader.read();
                if (piece.done) break;
                buffer += decoder.decode(piece.value, { stream: true }).replace(/\r\n/g, '\n');
                let cut = buffer.indexOf('\n\n');
                while (cut !== -1) {
                    const event = parseEvent(buffer.slice(0, cut));
                    buffer = buffer.slice(cut + 2);
                    cut = buffer.indexOf('\n\n');
                    if (!event) continue;
                    if (event.type === 'question') {
                        if (event.message && event.message.id) live.questionId = event.message.id;
                    } else if (event.type === 'sources') {
                        live.sources = event.sources || [];
                        if (chat.live === live) {
                            chat.pendingSources = live.sources;
                            chat.pendingDocuments = event.documents || [];
                            chat.pendingExpansion = event.expansion || null;
                            chat.pendingWarning = event.warning || null;
                            renderChatSources();
                        }
                    } else if (event.type === 'delta') {
                        live.answer += event.text || '';
                        paint(false);
                    } else if (event.type === 'done') {
                        done = event;
                    } else if (event.type === 'error') {
                        failed = event.error || 'модель не ответила';
                    }
                }
            }
        } catch (error) {
            if (error && error.name === 'AbortError') aborted = true;
            else failed = errorText(error);
        } finally {
            // Разбираем за собой только СВОЙ ответ. Инженер мог за это время
            // уйти в другой разговор и спросить там: раньше завершение
            // первого потока гасило кнопку «Стоп» второго и убирало с экрана
            // его печатающийся ответ.
            live.running = false;
            live.controller = null;
            syncStreaming();
        }

        if (chat.live === live) chat.live = null;

        if (done) {
            // Разговор мог быть закрыт или переключён, пока шёл ответ:
            // в историю кладём только если инженер всё ещё здесь.
            if (chat.current && String(chat.current.id) === String(live.chatId)) {
                chat.current = done.chat || chat.current;
                // Вопрос мог уже приехать в списке сообщений, если инженер
                // возвращался в раздел, пока шёл ответ.
                const have = {};
                chat.messages.forEach((item) => { have[String(item.id)] = true; });
                [done.question, done.answer].forEach((item) => {
                    if (item && !have[String(item.id)]) chat.messages.push(item);
                });
                // Приложенные файлы привязываем к отправленному вопросу:
                // без этого они пропадали с экрана сразу после ответа и
                // возвращались только при следующем открытии разговора.
                if (done.question && live.attachments.length) {
                    chat.sentAttachments = (chat.sentAttachments || []).concat(
                        live.attachments.map((item) => Object.assign(
                            {}, item, { message_id: done.question.id })));
                }
                chat.sourcesOf = done.answer;
                chat.pendingSources = null;
                renderFeed();
                renderChatSources();
                renderTalkHead();
            }
            upsertChatInList(done.chat || chat.current);
            return;
        }

        if (!live.body || !live.body.isConnected) {
            // Экран не на месте — сказать некому и негде. Ответ, если он
            // успел появиться, сервер сохранил сам.
            if (failed) toast('Помощник: ' + failed, 'error');
            return;
        }
        live.body.classList.remove('is-typing');
        if (live.note) live.note.textContent = '';
        renderAnswer(live.body, live.answer, live.sources);
        if (failed) {
            live.body.appendChild(h('div', { class: 'msg-error' }, 'Ошибка: ' + failed));
        } else if (aborted) {
            live.body.appendChild(h('div', { class: 'msg-note' },
                'Генерация прервана. Написанное до остановки сохранено в разговоре.'));
        } else {
            live.body.appendChild(h('div', { class: 'msg-error' },
                'Поток оборвался, не дойдя до конца ответа.'));
        }
        scrollFeed();
    }

    // =====================================================================
    // 11. Личный кабинет
    // =====================================================================

    // -- сотрудники ---------------------------------------------------------

    /** Документы сотрудника отдельным окном: список сотрудников и так плотный. */
    function openPersonFiles(user) {
        const dialog = openModal({
            title: 'Документы — ' + (user.full_name || user.login),
            body: [
                h('dl', { class: 'kv', style: { marginBottom: '10px' } },
                    h('dt', {}, 'Должность'), h('dd', {}, roleLabel(user.role)),
                    user.phone ? h('dt', {}, 'Телефон') : null,
                    user.phone ? h('dd', {}, user.phone) : null,
                    user.ext_no ? h('dt', {}, 'Внутренний') : null,
                    user.ext_no ? h('dd', { class: 'mono' }, user.ext_no) : null,
                    user.room ? h('dt', {}, 'Кабинет') : null,
                    user.room ? h('dd', {}, user.room) : null,
                    user.email ? h('dt', {}, 'Почта') : null,
                    user.email ? h('dd', {}, user.email) : null),
                personFilesCard(user.id, false),
            ],
            footer: [
                h('span', { class: 'spacer' }),
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Закрыть'),
            ],
        });
    }

    async function renderUsers(view) {
        clear(view);
        const page = h('div', { class: 'page' });
        view.appendChild(page);

        if (!isAdmin()) {
            page.appendChild(h('div', { class: 'card card-pad' },
                h('h3', {}, 'Раздел закрыт'),
                h('div', { class: 'muted' },
                    'Личным составом занимается начальник группы и выше.')));
            return;
        }

        const tableBox = h('div', {});
        const rolesBox = h('div', { class: 'card card-pad' });
        const pendingBox = h('div', { class: 'card card-pad' });
        const data = { roles: [], items: [], pending: [] };

        function role(roleId) {
            return data.roles.filter((item) => item.id === roleId)[0] || null;
        }

        function roleNote(roleId) {
            const found = role(roleId);
            return found ? found.note : '';
        }

        async function reload() {
            const fresh = await api.get('/api/users');
            data.roles = fresh.roles || [];
            data.items = fresh.items || [];
            // Заявки грузим отдельно: список сотрудников их не показывает —
            // до одобрения это ещё не сотрудник.
            try {
                const waiting = await api.get('/api/users/pending');
                data.pending = waiting.items || [];
            } catch (error) {
                data.pending = [];
            }
            paintRoles();
            paintPending();
            paint();
        }

        /* Заявки на доступ. Человек завёл себя сам через окно входа; войти он
           не сможет, пока должность ему не назначит начальство. Держим их
           наверху экрана: заявка, о которой забыли, — это человек, который
           не может работать. */
        function paintPending() {
            clear(pendingBox);
            pendingBox.hidden = !data.pending.length;
            if (!data.pending.length) return;
            pendingBox.appendChild(h('div', { class: 'card-title' },
                'Заявки на доступ',
                h('span', { class: 'badge badge--warn' }, String(data.pending.length))));
            pendingBox.appendChild(h('div', { class: 'muted small' },
                'Человек завёл себя сам в окне входа. Назначьте должность — '
                + 'до этого войти он не может.'));
            const allowed = data.roles.filter((item) => item.allowed);
            data.pending.forEach((item) => {
                const roleSelect = h('select', {}, allowed.map((role) => h('option', {
                    value: role.id, selected: role.id === 'engineer', title: role.note,
                }, role.title)));
                const team = h('input', {
                    type: 'text', class: 'input--quiet', placeholder: 'группа',
                });
                pendingBox.appendChild(h('div', { class: 'pending-row' },
                    h('div', { class: 'pending-who' },
                        h('b', {}, item.full_name || item.login),
                        h('span', { class: 'mono small muted' }, item.login),
                        h('span', { class: 'small faint' },
                            'подана ' + fmtDateTime(item.created_at))),
                    roleSelect,
                    team,
                    h('div', { class: 'row-actions nowrap' },
                        h('button', {
                            class: 'btn btn--sm btn--primary',
                            onclick: () => approveUser(item, roleSelect.value, team.value.trim()),
                        }, 'Одобрить'),
                        h('button', {
                            class: 'btn btn--sm btn--danger',
                            title: 'Заявка будет удалена',
                            onclick: () => rejectUser(item),
                        }, 'Отклонить'))));
            });
        }

        async function approveUser(item, role, team) {
            try {
                await api.post('/api/users/' + item.id + '/approve', { role: role, team: team });
                toast('Доступ открыт: ' + (item.full_name || item.login));
                await reload();
            } catch (error) {
                toastError(error);
            }
        }

        async function rejectUser(item) {
            const ok = await confirmDialog({
                title: 'Отклонить заявку?',
                message: 'Запись «' + (item.full_name || item.login)
                    + '» будет удалена. Человек сможет подать заявку заново.',
                confirmText: 'Отклонить', danger: true,
            });
            if (!ok) return;
            try {
                await api.post('/api/users/' + item.id + '/reject', {});
                await reload();
            } catch (error) {
                toastError(error);
            }
        }

        /* Штатное расписание: что даёт каждая должность. Список приходит
           с сервера — второй экземпляр в браузере с ним расходился. */
        function paintRoles() {
            clear(rolesBox);
            rolesBox.appendChild(h('div', { class: 'card-title' }, 'Должности и права'));
            rolesBox.appendChild(h('div', { class: 'role-list' }, data.roles.map((item) =>
                h('div', { class: 'role-item' + (item.is_admin ? ' role-item--admin' : '') },
                    h('b', {}, item.title,
                        item.is_admin ? h('span', { class: 'badge badge--accent' }, 'администратор') : null),
                    h('span', { class: 'muted small' }, item.note)))));
        }

        function paint() {
            clear(tableBox);
            const body = h('tbody', {});
            data.items.forEach((user) => {
                const locked = !user.may_manage;
                const nameInput = h('input', {
                    type: 'text', value: user.full_name || '',
                    placeholder: 'Фамилия И. О.', class: 'input--quiet',
                    disabled: locked,
                    onchange: () => save(user, { full_name: nameInput.value }),
                });
                const roleSelect = h('select', {
                    class: 'select--quiet',
                    title: locked ? 'Должность этого сотрудника менять нельзя' : roleNote(user.role),
                    disabled: locked,
                    onchange: () => save(user, { role: roleSelect.value }, roleSelect),
                }, data.roles.map((item) => h('option', {
                    value: item.id,
                    selected: user.role === item.id,
                    // Должность выше собственной сервер всё равно не примет —
                    // лучше не предлагать её в списке.
                    disabled: !item.allowed && user.role !== item.id,
                    title: item.note,
                }, item.title)));
                const depInput = h('input', {
                    type: 'text', value: user.department || '', class: 'input--quiet',
                    placeholder: '—', disabled: locked,
                    title: 'Подразделение, в котором сотрудник стоит по штату. '
                        + 'Работают все в отделе; поле нужно только тем, кто '
                        + 'числится в другом подразделении',
                    onchange: () => save(user, { department: depInput.value }),
                });
                const teamInput = h('input', {
                    type: 'text', value: user.team || '', class: 'input--quiet',
                    placeholder: '—', disabled: locked,
                    onchange: () => save(user, { team: teamInput.value }),
                });

                body.appendChild(h('tr', { class: user.active ? '' : 'is-off' },
                    h('td', { class: 'primary mono' }, user.login),
                    h('td', {}, nameInput),
                    h('td', {}, roleSelect),
                    h('td', {}, depInput),
                    h('td', {}, teamInput),
                    h('td', {}, user.active
                        ? h('span', { class: 'badge badge--ok' }, 'работает')
                        : h('span', { class: 'badge' }, 'отключён')),
                    h('td', { class: 'small muted nowrap' }, fmtDateTime(user.created_at)),
                    h('td', { class: 'row-actions nowrap' },
                        // Справка открыта только тому кругу, что проверяет
                        // отчёты: начальник отдела, заместитель, создатель.
                        // Начальник группы заводит людей, но объективку не
                        // читает — это личные сведения.
                        canReview() ? h('button', {
                            class: 'btn btn--sm',
                            title: 'Справка-объективка, приказы и прочие документы',
                            onclick: () => openPersonFiles(user),
                        }, 'Документы') : null,
                        h('button', {
                            class: 'btn btn--sm',
                            disabled: locked,
                            title: 'Задать новый пароль. Старый знать не нужно.',
                            onclick: () => resetPassword(user),
                        }, 'Пароль'),
                        h('button', {
                            class: 'btn btn--sm',
                            // Себя вызывать незачем, отключённому вызов не дойдёт.
                            disabled: !user.active || user.id === (state.user || {}).id,
                            title: 'Уведомление со звуком: подойти в кабинет',
                            onclick: () => callToOffice(user),
                        }, 'Вызвать'),
                        h('button', {
                            class: 'btn btn--sm',
                            disabled: locked,
                            title: user.active
                                ? 'Человек больше не сможет войти, данные останутся'
                                : 'Вернуть доступ',
                            onclick: () => setActive(user, !user.active),
                        }, user.active ? 'Отключить' : 'Включить'))));
            });

            const usersTable = h('table', { class: 'grid grid--users' },
                h('thead', {}, h('tr', {},
                    h('th', {}, 'Логин'),
                    h('th', {}, 'Фамилия и инициалы'),
                    h('th', {}, 'Должность'),
                    h('th', {}, 'По штату'),
                    h('th', {}, 'Группа'),
                    h('th', {}, 'Доступ'),
                    h('th', {}, 'Заведён'),
                    h('th', {}))),
                body);
            tableBox.appendChild(h('div', { class: 'table-scroll' },
                makeResizable(usersTable, 'users')));
        }

        async function save(user, patch, control) {
            try {
                const fresh = await api.patch('/api/users/' + user.id, patch);
                Object.assign(user, fresh.user);
                toast('Сохранено: ' + (user.full_name || user.login));
                paint();
            } catch (error) {
                toastError(error);
                if (control) control.value = user.role;
                paint();
            }
        }

        /* «Вызвать в кабинет» — то, что в отделе делают голосом через
           коридор. Уведомление приходит со звуком, чтобы человек за
           наушниками его не пропустил. */
        async function callToOffice(user) {
            const place = h('input', { type: 'text', placeholder: 'например, каб. 214' });
            const note = h('textarea', { rows: 3, placeholder: 'с чем подойти (не обязательно)' });
            const submit = h('button', {
                class: 'btn btn--primary',
                onclick: async () => {
                    submit.disabled = true;
                    try {
                        await api.post('/api/notifications/call', {
                            user_id: user.id,
                            place: place.value.trim(),
                            note: note.value.trim(),
                        });
                        dialog.close();
                        toast('Вызов отправлен: ' + (user.full_name || user.login));
                    } catch (error) {
                        toastError(error);
                    } finally {
                        submit.disabled = false;
                    }
                },
            }, 'Вызвать');
            const dialog = openModal({
                title: 'Вызвать в кабинет: ' + (user.full_name || user.login),
                narrow: true,
                body: [
                    h('div', { class: 'muted small' },
                        'Сотруднику придёт уведомление со звуком.'),
                    h('label', { class: 'field' }, 'Куда подойти', place),
                    h('label', { class: 'field field-area' }, 'Примечание', note),
                ],
                footer: [
                    h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                    submit,
                ],
            });
            place.focus();
        }

        async function resetPassword(user) {
            const value = await promptDialog({
                title: 'Новый пароль для «' + (user.full_name || user.login) + '»',
                message: 'Не короче 8 символов. Старый пароль знать не нужно. ' +
                    'Все открытые сеансы этого сотрудника закроются.',
                password: true,
                confirmText: 'Задать пароль',
            });
            if (!value) return;
            try {
                await api.post('/api/users/' + user.id + '/password', { password: value });
                toast('Пароль изменён, сеансы закрыты');
            } catch (error) {
                toastError(error);
            }
        }

        async function setActive(user, active) {
            if (!active) {
                const ok = await confirmDialog({
                    title: 'Отключить сотрудника?',
                    message: '«' + (user.full_name || user.login) + '» больше не сможет войти. ' +
                        'Его отчёты и правки останутся на месте — доступ можно вернуть.',
                    confirmText: 'Отключить', danger: true,
                });
                if (!ok) return;
            }
            try {
                await api.post('/api/users/' + user.id + '/active', { active: active });
                await reload();
            } catch (error) {
                toastError(error);
            }
        }

        async function addUser() {
            const login = h('input', { type: 'text', placeholder: 'petrov', autocapitalize: 'off' });
            const fullName = h('input', { type: 'text', placeholder: 'Петров П. П.' });
            const passwordBox = passwordField('не короче 8 символов');
            const password = passwordBox.input;
            const allowed = data.roles.filter((item) => item.allowed);
            const roleSelect = h('select', {}, allowed.map((item) => h('option', {
                value: item.id, selected: item.id === 'engineer',
            }, item.title)));
            // Отдел у всех один — 2СО, и спрашивать его у каждого незачем.
            // Здесь только штатная принадлежность: человек работает в отделе,
            // а по штату может стоять в другом подразделении.
            const department = h('input', {
                type: 'text', placeholder: 'если по штату в другом подразделении',
            });
            const team = h('input', { type: 'text' });
            const note = h('div', { class: 'small muted' }, roleNote(roleSelect.value));
            roleSelect.addEventListener('change', () => {
                note.textContent = roleNote(roleSelect.value);
            });

            // Кнопку держим в переменной. Раньше её включали обратно через
            // event.currentTarget, а он после await равен null: сервер
            // отклонял короткий пароль, обработчик падал на «Cannot set
            // properties of null», и кнопка оставалась выключенной навсегда.
            const submit = h('button', {
                class: 'btn btn--primary',
                onclick: async () => {
                    submit.disabled = true;
                    try {
                        await api.post('/api/users', {
                            login: login.value.trim(),
                            full_name: fullName.value.trim(),
                            password: password.value,
                            role: roleSelect.value,
                            department: department.value.trim(),
                            team: team.value.trim(),
                        });
                        dialog.close();
                        toast('Сотрудник заведён');
                        await reload();
                    } catch (error) {
                        toastError(error);
                    } finally {
                        submit.disabled = false;
                    }
                },
            }, 'Завести');

            const dialog = openModal({
                title: 'Новый сотрудник',
                narrow: true,
                body: [
                    h('div', { class: 'form-grid' },
                        h('label', { class: 'field' }, 'Логин для входа', login,
                            h('span', { class: 'small faint' }, 'латиница, 3–32 знака')),
                        h('label', { class: 'field' }, 'Фамилия и инициалы', fullName),
                        h('label', { class: 'field' }, 'Первый пароль', passwordBox),
                        h('label', { class: 'field' }, 'Должность', roleSelect),
                        h('label', { class: 'field' }, 'По штату', department),
                        h('label', { class: 'field' }, 'Группа', team)),
                    note,
                ],
                footer: [
                    h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                    submit,
                ],
            });
            login.focus();
        }

        append(page, [
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' }, 'Личный состав отдела, должности и доступ'),
                h('div', { class: 'page-head-actions' },
                    h('button', { class: 'btn', onclick: () => reload() }, 'Обновить'),
                    h('button', {
                        class: 'btn btn--primary', onclick: () => addUser(),
                    }, 'Завести сотрудника'))),
            pendingBox,
            rolesBox,
            tableBox,
        ]);

        await reload();
    }

    /* ------------------------------------------------------- сообщения ---

       Переписка между людьми отдела: личная и на несколько человек. Половина
       вопросов по письму решается одной фразой «глянь, это тот же ствол?» —
       и раньше её говорили в коридор или писали в стороннем мессенджере,
       которого на изолированной машине нет.

       Экран устроен как почта: слева беседы, справа выбранная. Опрос раз в
       десять секунд — соединения, которое держат открытым, здесь не нужно:
       людей в отделе десятки, а не тысячи. */

    const TALK_POLL_MS = 10000;
    const talks = { items: [], current: null, timer: null, nodes: {} };

    function stopTalkPoll() {
        if (talks.timer) {
            clearInterval(talks.timer);
            talks.timer = null;
        }
    }

    /** Как назвать беседу: своим именем либо по собеседникам. */
    function talkTitle(item) {
        if (item.title) return item.title;
        const me = (state.user || {}).id;
        const others = (item.members || []).filter((member) => member.id !== me);
        if (!others.length) return 'Заметки для себя';
        return others.map((member) => member.full_name).join(', ');
    }

    async function renderTalks(view, talkId) {
        clear(view);
        const listBox = h('div', { class: 'talk-list' });
        const roomBox = h('div', { class: 'talk-room' });
        talks.nodes = { list: listBox, room: roomBox };
        talks.current = talkId ? Number(talkId) : null;

        view.appendChild(h('div', { class: 'page page--talks' },
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' },
                    'Переписка отдела: личная и на несколько человек'),
                h('div', { class: 'page-head-actions' },
                    h('button', { class: 'btn', onclick: () => loadTalks() }, 'Обновить'),
                    h('button', {
                        class: 'btn btn--primary', onclick: () => openNewTalk(),
                    }, 'Написать'))),
            h('div', { class: 'talks' }, listBox, roomBox)));

        await loadTalks();
        stopTalkPoll();
        // Опрос заводим после первой загрузки, чтобы два запроса не пошли
        // одновременно, и снимаем при уходе с экрана — см. renderRoute.
        talks.timer = setInterval(() => {
            if (state.route && state.route.name === 'talks') loadTalks(true);
            else stopTalkPoll();
        }, TALK_POLL_MS);
    }

    async function loadTalks(quiet) {
        try {
            const data = await api.get('/api/talks');
            talks.items = data.items || [];
        } catch (error) {
            if (!quiet) toastError(error);
            talks.items = [];
        }
        // Свежая беседа открывается сама. Раздел с одним разговором и пустой
        // правой половиной выглядит сломанным: человек зашёл читать, а ему
        // предлагают сначала выбрать — из одного.
        if (!talks.current && talks.items.length) talks.current = talks.items[0].id;
        paintTalkList();
        setNavCount('talks', talks.items.reduce((sum, item) => sum + (item.unread || 0), 0));
        if (talks.current) await openTalk(talks.current, true);
        else paintTalkRoom(null);
    }

    function paintTalkList() {
        const box = talks.nodes.list;
        if (!box) return;
        clear(box);
        if (!talks.items.length) {
            box.appendChild(h('div', { class: 'small faint pad' },
                'Бесед пока нет. «Написать» — и выберите, кому.'));
            return;
        }
        talks.items.forEach((item) => {
            box.appendChild(h('button', {
                class: 'talk-row' + (item.id === talks.current ? ' is-active' : ''),
                onclick: () => {
                    location.hash = '#/talks/' + item.id;
                },
            },
                h('div', { class: 'talk-row-head' },
                    h('b', { class: 'grow' }, talkTitle(item)),
                    item.unread ? h('span', { class: 'badge badge--accent' },
                        String(item.unread)) : null),
                h('div', { class: 'small muted talk-last' }, item.last_text || '—'),
                h('div', { class: 'small faint' }, fmtDateTime(item.updated_at))));
        });
    }

    async function openTalk(talkId, quiet) {
        try {
            const data = await api.get('/api/talks/' + talkId);
            talks.current = talkId;
            paintTalkRoom(data);
        } catch (error) {
            if (!quiet) toastError(error);
            paintTalkRoom(null);
        }
    }

    function paintTalkRoom(data) {
        const box = talks.nodes.room;
        if (!box) return;
        // Запоминаем, был ли человек внизу: дочитанную до конца переписку
        // прокручиваем к новому сообщению, а поднятую вверх — не дёргаем.
        const stream = talks.nodes.stream;
        const wasDown = !stream || (stream.scrollHeight - stream.scrollTop - stream.clientHeight) < 60;
        clear(box);
        if (!data) {
            talks.nodes.stream = null;
            box.appendChild(h('div', { class: 'empty' },
                h('h3', {}, 'Беседа не выбрана'),
                h('div', {}, 'Слева — то, что уже есть. «Написать» — новая.')));
            return;
        }

        const me = (state.user || {}).id;
        const messages = data.messages || [];
        const flow = h('div', { class: 'talk-stream' }, messages.map((message) =>
            h('div', { class: 'talk-msg' + (message.user_id === me ? ' talk-msg--mine' : '') },
                h('div', { class: 'talk-msg-head' },
                    h('b', {}, message.author || 'кто-то'),
                    h('span', { class: 'small faint' }, fmtDateTime(message.created_at))),
                h('div', { class: 'talk-msg-text' }, message.text || ''))));
        talks.nodes.stream = flow;

        const field = h('textarea', {
            rows: 2, placeholder: 'Сообщение. Ctrl+Enter — отправить',
            onkeydown: (event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault();
                    send();
                }
            },
        });
        const button = h('button', { class: 'btn btn--primary', onclick: () => send() }, 'Отправить');

        async function send() {
            const text = field.value.trim();
            if (!text) return;
            button.disabled = true;
            try {
                await api.post('/api/talks/' + data.id + '/messages', { text: text });
                field.value = '';
                await loadTalks(true);
            } catch (error) {
                toastError(error);
            } finally {
                button.disabled = false;
                field.focus();
            }
        }

        append(box, [
            h('div', { class: 'talk-head' },
                h('b', { class: 'grow' }, talkTitle({
                    title: (talks.items.filter((item) => item.id === data.id)[0] || {}).title || '',
                    members: data.members,
                })),
                h('span', { class: 'small faint' },
                    (data.members || []).length + ' чел.')),
            flow,
            h('div', { class: 'talk-send' }, field, button),
        ]);
        if (wasDown) flow.scrollTop = flow.scrollHeight;
    }

    /* Кому писать. Список берём из сводки отдела: он доступен всем, в отличие
       от раздела «Сотрудники», куда рядового инженера не пускают. */
    async function openNewTalk() {
        let people = [];
        try {
            const board = await api.get('/api/board');
            const me = (state.user || {}).id;
            people = (board.people || []).filter(
                (person) => person.active && person.id !== me);
        } catch (error) {
            toastError(error);
            return;
        }
        if (!people.length) {
            toast('Писать некому: в отделе один человек', 'error');
            return;
        }

        const checks = people.map((person) => {
            const box = h('input', { type: 'checkbox', value: String(person.id) });
            return {
                id: person.id,
                box: box,
                node: h('label', { class: 'check-row' }, box,
                    h('span', {}, person.full_name,
                        h('span', { class: 'small faint' }, ' · ' + person.role_title))),
            };
        });
        const title = h('input', {
            type: 'text', placeholder: 'если беседа не на двоих — как её назвать',
        });
        const submit = h('button', {
            class: 'btn btn--primary',
            onclick: async () => {
                const chosen = checks.filter((item) => item.box.checked).map((item) => item.id);
                if (!chosen.length) {
                    toast('Выберите, кому писать', 'error');
                    return;
                }
                submit.disabled = true;
                try {
                    const data = await api.post('/api/talks', {
                        members: chosen, title: title.value.trim(),
                    });
                    dialog.close();
                    location.hash = '#/talks/' + data.talk_id;
                    await loadTalks(true);
                } catch (error) {
                    toastError(error);
                } finally {
                    submit.disabled = false;
                }
            },
        }, 'Завести беседу');

        const dialog = openModal({
            title: 'Кому написать',
            narrow: true,
            body: [
                h('div', { class: 'small muted' },
                    'Отметьте одного — получится личная переписка; '
                    + 'нескольких — общая беседа.'),
                h('div', { class: 'check-list' }, checks.map((item) => item.node)),
                h('label', { class: 'field' }, 'Название беседы', title),
            ],
            footer: [
                h('button', { class: 'btn', onclick: () => dialog.close() }, 'Отмена'),
                submit,
            ],
        });
    }

    async function renderMe(view) {
        clear(view);
        const page = h('div', { class: 'page page--narrow' });
        view.appendChild(page);

        const data = await api.get('/api/me/summary');
        const user = data.user || state.user || {};
        const reports = data.reports || {};
        const edits = data.edits || {};

        const card = h('div', { class: 'card card-pad' },
            h('div', { class: 'card-title' }, 'Учётная запись'),
            h('dl', { class: 'kv' },
                h('dt', {}, 'Логин'), h('dd', { class: 'mono' }, user.login || '—'),
                h('dt', {}, 'ФИО'), h('dd', {}, user.full_name || '—'),
                h('dt', {}, 'Должность'), h('dd', {}, roleLabel(user.role)),
                // Отдел у всех один: это и есть система. Спрашивать его у
                // каждого незачем — берём из названия.
                h('dt', {}, 'Отдел'),
                h('dd', {}, brandName() + (user.team ? ', ' + user.team : '')),
                user.department ? h('dt', {}, 'По штату') : null,
                user.department ? h('dd', {}, user.department) : null,
                h('dt', {}, 'Права'), h('dd', { class: 'small muted' }, rolePowers(user.role))));

        const cards = h('div', { class: 'stat-cards' },
            statCard(data.my_cases_total || 0, 'писем за вами',
                (data.overdue || 0) ? 'просрочено: ' + data.overdue : 'просрочек нет'),
            statCard(data.sent || 0, 'ответов отправлено вами',
                'записан исходящий номер — письмо закрыто'),
            statCard(reports.total || 0, 'редакций отчётов',
                'из них проверено: ' + (reports.approved || 0)),
            statCard(data.cases || 0, 'писем зарегистрировано вами'),
            statCard(edits.pairs || 0, 'пар «черновик → готовое»',
                'средняя доля правки: ' + fmtNumber(edits.mean_distance || 0, 3)),
            statCard(data.chats || 0, 'разговоров с помощником',
                'чужие разговоры недоступны никому'));

        append(page, [
            h('div', { class: 'page-head' },
                h('div', { class: 'page-note' }, 'Ваши данные, работа, документы и пароль'),
                h('div', { class: 'page-head-actions' },
                    h('button', {
                        class: 'btn', onclick: () => renderRoute(state.route),
                    }, 'Обновить'))),
            card,
            cards,
            myCasesCard(data),
            myRosterCard(data),
            contactsCard(user),
            personFilesCard(user.id, true),
            passwordCard(),
            themeCard(),
        ]);
    }

    /* Что за человеком числится прямо сейчас. Кабинет должен отвечать не
       только «сколько я сделал», но и «что мне делать»: за вторым приходят
       чаще, а раньше за этим приходилось идти в список писем и фильтровать. */
    function myCasesCard(data) {
        const items = data.my_cases || [];
        const box = h('div', { class: 'card card-pad' },
            h('div', { class: 'toolbar' },
                h('span', { class: 'card-title grow' }, 'Письма за вами'),
                h('a', { class: 'btn btn--sm', href: '#/cases' }, 'Все письма')));
        if (!items.length) {
            box.appendChild(h('div', { class: 'muted' },
                'За вами нет писем в работе.'));
            return box;
        }
        const rows = items.map((item) => h('tr', {
            class: 'clickable',
            onclick: () => navigate('#/case/' + item.id),
        },
            h('td', { class: 'mono nowrap small' }, item.incoming_no || item.case_id),
            h('td', {}, item.title || h('span', { class: 'faint' }, 'без описания')),
            h('td', { class: 'nowrap' }, deadlineCell(item)),
            h('td', {}, statusBadge(item.status))));
        box.appendChild(h('div', { class: 'table-scroll' },
            h('table', { class: 'grid' },
                h('thead', {}, h('tr', {},
                    h('th', {}, 'Входящий'), h('th', {}, 'Описание'),
                    h('th', {}, 'Срок ответа'), h('th', {}, 'Состояние'))),
                h('tbody', {}, rows))));
        if ((data.my_cases_total || 0) > items.length) {
            box.appendChild(h('div', { class: 'small faint', style: { marginTop: '6px' } },
                'показаны ' + items.length + ' из ' + data.my_cases_total));
        }
        return box;
    }

    /** Свой расход на две недели вперёд: сюда заходят свериться, где я завтра. */
    function myRosterCard(data) {
        const items = data.roster || [];
        const box = h('div', { class: 'card card-pad' },
            h('div', { class: 'toolbar' },
                h('span', { class: 'card-title grow' }, 'Ваш расход на две недели'),
                h('a', { class: 'btn btn--sm', href: '#/roster' }, 'Весь расход')));
        if (!items.length) {
            append(box, [
                h('div', { class: 'muted' },
                    'Вы себя не отметили. Расход отдела собирается из таких отметок — '
                    + 'без них начальник не знает, где вас искать.'),
                h('div', { class: 'btn-row', style: { marginTop: '10px' } },
                    h('button', {
                        class: 'btn btn--primary',
                        onclick: () => openRosterDialog(
                            { user_id: (state.user || {}).id, date_from: todayIso() },
                            () => renderRoute(state.route)),
                    }, 'Отметить себя')),
            ]);
            return box;
        }
        box.appendChild(h('div', { class: 'roster-mine' }, items.map((item) =>
            h('button', {
                class: 'roster-mine-row kind-' + (ROSTER_KIND[item.kind] || {}).cls,
                title: 'Поправить отметку',
                onclick: () => openRosterDialog(item, () => renderRoute(state.route)),
            },
                h('b', {}, (ROSTER_KIND[item.kind] || {}).title || item.kind),
                h('span', {}, item.date_from === item.date_to
                    ? fmtDate(item.date_from)
                    : fmtDate(item.date_from) + ' — ' + fmtDate(item.date_to)),
                item.place ? h('i', {}, item.place) : null))));
        return box;
    }

    /* Контакты человек правит сам: справочник, который ведёт кадровик,
       устаревает быстрее, чем его правят, а свой внутренний номер человек
       поправит в ту же минуту, когда переедет. */
    function contactsCard(user) {
        const note = h('div', { class: 'form-note' });
        const fields = {
            phone: h('input', { type: 'tel', value: user.phone || '',
                placeholder: '+7 900 000-00-00', maxLength: 120 }),
            ext_no: h('input', { type: 'text', value: user.ext_no || '',
                placeholder: '3-45', maxLength: 120, class: 'mono' }),
            room: h('input', { type: 'text', value: user.room || '',
                placeholder: '214', maxLength: 120 }),
            email: h('input', { type: 'email', value: user.email || '',
                placeholder: 'ivanov@otdel', maxLength: 120 }),
        };
        const save = h('button', { class: 'btn btn--primary', onclick: submit }, 'Сохранить');

        async function submit() {
            save.disabled = true;
            try {
                const payload = {};
                Object.keys(fields).forEach((key) => { payload[key] = fields[key].value.trim(); });
                const data = await api.patch('/api/me/contacts', payload);
                if (state.user) Object.assign(state.user, data.user || {});
                note.textContent = 'Контакты сохранены.';
                note.className = 'form-note is-ok';
            } catch (error) {
                note.textContent = errorText(error);
                note.className = 'form-note is-bad';
            } finally {
                save.disabled = false;
            }
        }

        return h('div', { class: 'card card-pad' },
            h('div', { class: 'card-title' }, 'Как вас найти'),
            h('div', { class: 'small muted', style: { marginBottom: '10px' } },
                'Видно отделу в расходе и в списке сотрудников. Заполняете вы сами.'),
            h('div', { class: 'form-grid' },
                h('label', { class: 'field' }, 'Телефон', fields.phone),
                h('label', { class: 'field' }, 'Внутренний', fields.ext_no),
                h('label', { class: 'field' }, 'Кабинет', fields.room),
                h('label', { class: 'field' }, 'Почта', fields.email)),
            h('div', { class: 'btn-row', style: { marginTop: '10px' } }, save),
            note);
    }

    /* Документы сотрудника: справка-объективка, приказы, прочее. Свои видит
       каждый, чужие — начальник отдела, заместитель и создатель системы.
       Начальник группы сюда не входит, хотя он и администратор: это личные
       сведения, и круг тех, кому они открыты, уже круга тех, кто заводит
       учётные записи.

       Справка-объективка одна: новая заменяет прежнюю, иначе список копит
       редакции и непонятно, какая действующая. Приказы и прочее копятся. */
    function personFilesCard(userId, mine) {
        const box = h('div', { class: 'card card-pad' });
        const listBox = h('div', {});
        let canEditFiles = false;

        const picker = h('input', {
            type: 'file', style: { display: 'none' },
            onchange: async (event) => {
                const file = (event.target.files || [])[0];
                event.target.value = '';
                if (!file) return;
                const form = new FormData();
                form.append('file', file);
                form.append('kind', kindPick.value);
                try {
                    await uploadFile('/api/users/' + userId + '/files', form);
                    toast('Документ загружен', 'ok');
                } catch (error) {
                    toastError(error);
                }
                await load();
            },
        });
        // Виды документов приходят с сервера: список один на сервер и экран,
        // разойтись им нельзя.
        const kindPick = h('select', { title: 'Вид документа' });

        function fillKinds(kinds) {
            if (kindPick.options.length) return;
            (kinds || []).forEach((kind) => kindPick.appendChild(
                h('option', { value: kind.id }, kind.title)));
        }

        async function load() {
            clear(listBox);
            let data;
            try {
                data = await api.get('/api/users/' + userId + '/files');
            } catch (error) {
                listBox.appendChild(h('div', { class: 'small muted' }, errorText(error)));
                return;
            }
            canEditFiles = Boolean(data.can_edit);
            fillKinds(data.kinds);
            const items = data.files || [];
            if (!items.length) {
                listBox.appendChild(h('div', { class: 'muted' }, mine
                    ? 'Справка-объективка не загружена.'
                    : 'Документов нет.'));
                return;
            }
            listBox.appendChild(h('div', { class: 'file-list' }, items.map((item) =>
                h('div', { class: 'file-row' },
                    h('button', {
                        class: 'file-name file-name--link',
                        title: 'Посмотреть ' + item.name,
                        onclick: () => openFilePreview(item,
                            '/api/users/' + userId + '/files/' + item.id),
                    }, item.name),
                    h('span', { class: 'tag' }, item.kind_title),
                    h('span', { class: 'small faint nowrap' }, fmtBytes(item.size)),
                    canEditFiles ? h('button', {
                        class: 'btn btn--icon btn--danger-hover',
                        title: 'Убрать документ',
                        onclick: () => removeFile(item),
                    }, iconGlyph('trash')) : null))));
        }

        async function removeFile(item) {
            const ok = await confirmDialog({
                title: 'Убрать документ',
                message: 'Документ «' + item.name + '» будет убран и удалён с диска.',
                confirmText: 'Убрать',
                danger: true,
            });
            if (!ok) return;
            try {
                await api.del('/api/users/' + userId + '/files/' + item.id);
                toast('Документ убран', 'ok');
            } catch (error) {
                toastError(error);
            }
            await load();
        }

        append(box, [
            h('div', { class: 'toolbar' },
                h('span', { class: 'card-title grow' },
                    mine ? 'Ваши документы' : 'Документы сотрудника'),
                kindPick,
                h('button', {
                    class: 'btn btn--sm', onclick: () => picker.click(),
                }, iconGlyph('clip'), 'Загрузить'),
                picker),
            h('div', { class: 'small muted', style: { margin: '2px 0 10px' } },
                'Справка-объективка, приказы и прочее. Видите их вы, начальник '
                + 'отдела, его заместитель и создатель системы; остальному '
                + 'отделу они недоступны. Справка одна: новая заменяет прежнюю, '
                + 'остальное копится.'),
            listBox,
        ]);
        load();
        return box;
    }

    /* Что даёт должность. Формулировки совпадают с ROLE_NOTES на сервере:
       если разойдутся, человек прочитает в кабинете одно, а получит другое. */
    function rolePowers(role) {
        const base = 'письма и отчёты, пополнение библиотеки, помощник';
        const admin = base + '; сотрудники, удаление документов, журнал действий';
        return {
            owner: admin + '; может менять должность любому сотруднику',
            head: admin,
            deputy: admin,
            lead: admin,
            senior: base + '; утверждение отчётов',
            engineer: base,
        }[role] || base;
    }

    function passwordCard() {
        const box = h('div', { class: 'card card-pad' });
        const note = h('div', { class: 'form-note' });
        const currentBox = passwordField();
        const freshBox = passwordField('не короче 8 символов');
        const repeatBox = passwordField();
        const current = currentBox.input;
        const fresh = freshBox.input;
        const repeat = repeatBox.input;
        current.autocomplete = 'current-password';
        fresh.autocomplete = 'new-password';
        repeat.autocomplete = 'new-password';
        const button = h('button', { class: 'btn btn--primary', onclick: () => submit() }, 'Сменить пароль');

        const fail = (message) => {
            note.textContent = message;
            note.className = 'form-note is-bad';
        };
        const ok = (message) => {
            note.textContent = message;
            note.className = 'form-note is-ok';
        };

        async function submit() {
            const currentValue = current.value;
            const freshValue = fresh.value;
            if (!currentValue) {
                fail('Введите текущий пароль.');
                current.focus();
                return;
            }
            if (freshValue.length < 8) {
                fail('Новый пароль короче 8 символов.');
                fresh.focus();
                return;
            }
            if (freshValue !== repeat.value) {
                fail('Новый пароль и повтор не совпадают.');
                repeat.focus();
                return;
            }
            if (freshValue === currentValue) {
                fail('Новый пароль совпадает с текущим.');
                fresh.focus();
                return;
            }
            button.disabled = true;
            try {
                await api.post('/api/me/password', { current: currentValue, new: freshValue });
                current.value = '';
                fresh.value = '';
                repeat.value = '';
                ok('Пароль изменён. Остальные сессии закрыты, эта продолжает работать.');
                toast('Пароль изменён', 'ok');
            } catch (error) {
                if (error instanceof ApiError && error.status === 403) {
                    fail('Текущий пароль указан неверно.');
                    current.focus();
                    current.select();
                } else {
                    fail(errorText(error));
                }
            } finally {
                button.disabled = false;
            }
        }

        const onEnter = (event) => {
            if (event.key === 'Enter') submit();
        };
        [current, fresh, repeat].forEach((field) => field.addEventListener('keydown', onEnter));

        append(box, [
            h('div', { class: 'card-title' }, 'Смена пароля'),
            !state.authEnabled
                ? h('div', { class: 'small muted' },
                    'Аутентификация выключена настройками (локальный режим) — пароль не используется.')
                : [
                    h('div', { class: 'form-grid' },
                        h('label', { class: 'field' }, 'Текущий пароль', currentBox),
                        h('label', { class: 'field' }, 'Новый пароль', freshBox),
                        h('label', { class: 'field' }, 'Повтор нового пароля', repeatBox)),
                    note,
                    h('div', { class: 'btn-row', style: { marginTop: '12px' } }, button),
                ],
        ]);
        if (!state.authEnabled) {
            [current, fresh, repeat, button].forEach((field) => { field.disabled = true; });
        }
        return box;
    }

    function themeCard() {
        const box = h('div', { class: 'card card-pad' });
        const buttons = ['auto', 'light', 'dark'].map((mode) => h('button', {
            class: 'btn', dataset: { mode: mode },
            onclick: () => {
                storageSet('rg-theme', mode);
                applyTheme(mode);
                mark();
            },
        }, THEME_LABEL[mode]));

        function mark() {
            const active = storageGet('rg-theme', 'auto');
            buttons.forEach((button) => {
                button.classList.toggle('btn--primary', button.dataset.mode === active);
            });
        }

        mark();
        append(box, [
            h('div', { class: 'card-title' }, 'Оформление'),
            h('div', { class: 'small muted', style: { marginBottom: '10px' } },
                'Режим «авто» следует настройке светлой или тёмной темы в операционной системе. ' +
                'Выбор хранится в этом браузере.'),
            h('div', { class: 'btn-row' }, buttons),
        ]);
        return box;
    }

    // =====================================================================
    // 12. Запуск
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

        if (!location.hash) location.hash = '#/board';
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
