/**
 * BST Interactive Prototype Controller
 * Fetches, parses, groups, sorts, translates, and renders the Bible Structure and Timeline dataset.
 */

document.addEventListener('DOMContentLoaded', () => {
    // Relative paths to website JSON data and UI translations
    const JSON_PATH = './data/bst_interactive_data.json';
    const UI_TRANSLATIONS_PATH = './i18n/ui-translations.json';
    
    // DOM Elements
    const langSelect = document.getElementById('language-select');
    
    // Application State
    let bstData = null;
    let uiTranslations = {};
    let currentLang = 'en';
    const expandedNodes = new Set();
    const expandedAboutNodes = new Set();
    const expandedScripturePassages = new Set();
    let isReferencesExpanded = false;
    let isScripturePermissionsExpanded = false;

    // Built-in English fallback strings for all 29 UI keys
    const UI_FALLBACK_STRINGS = {
        related_books: 'Related Books',
        people_and_events: 'People & Events',
        references_and_permissions: 'References & Permissions',
        scripture_sources_and_permissions: 'Scripture Sources & Permissions',
        legend_poetry_ot: 'Poetry (OT)',
        legend_letters_nt: 'Letters (NT)',
        legend_poetry_letters: 'Poetry (OT) / Letters (NT)',
        select_language: 'Select language',
        loading_placeholder: 'Loading...',
        loading_timeline: 'Loading timeline...',
        error_loading_timeline: 'Could not load timeline data. Please ensure the local web server is running.',
        switch_to_dark: 'Switch to dark mode',
        switch_to_light: 'Switch to light mode',
        color_key: 'Color key',
        hide_color_key: 'Hide color key',
        show_color_key: 'Show color key',
        opens_in_new_tab: '(opens in a new tab)',
        action_show: 'Show',
        action_hide: 'Hide',
        permission_public_domain: 'Public Domain',
        permission_cc_by_sa: 'CC BY-SA 4.0',
        permission_publisher_policy_compliant: 'Permitted by Publisher Policy',
        permission_fair_use_short_quote: 'Fair Use (Short Quotation)',
        permission_written_permission: 'Written Permission',
        permission_pending_verification: 'Pending Verification',
        show_book_info: 'Show information about {book}',
        hide_book_info: 'Hide information about {book}',
        show_book_events: 'Show events and people for {book}',
        hide_book_events: 'Hide events and people for {book}'
    };

    /**
     * Resolves a localized UI string for the active language with English fallback
     * and optional placeholder substitution (e.g. {book}).
     */
    function t(key, params = {}) {
        let str = (uiTranslations[currentLang] && uiTranslations[currentLang][key]) ||
                  (uiTranslations['en'] && uiTranslations['en'][key]) ||
                  UI_FALLBACK_STRINGS[key] ||
                  key;
        if (params && typeof params === 'object') {
            for (const [pKey, pVal] of Object.entries(params)) {
                str = str.replace(new RegExp(`\\{${pKey}\\}`, 'g'), pVal);
            }
        }
        return str;
    }

    // Expose test hooks for automated validation suites
    window.__bstTest = {
        t,
        setUiTranslations: (v) => { uiTranslations = v; },
        getUiTranslations: () => uiTranslations,
        UI_FALLBACK_STRINGS
    };

    // External URLs for specific bibliographic source_note references
    const SOURCE_NOTE_URLS = {
        interactive_timeline_of_bible_history: 'https://basicsofthebible.org/projects/interactivetimeline/',
        basics_of_the_bible_book: 'https://basicsofthebible.org/studyguide/'
    };

    // Mapping for controlled permission_status values to UI translation keys
    const PERMISSION_STATUS_KEY_MAP = {
        public_domain: 'permission_public_domain',
        cc_by_sa: 'permission_cc_by_sa',
        publisher_policy_compliant: 'permission_publisher_policy_compliant',
        fair_use_short_quote: 'permission_fair_use_short_quote',
        written_permission: 'permission_written_permission',
        pending_verification: 'permission_pending_verification'
    };

    const CC_BY_SA_4_URL = 'https://creativecommons.org/licenses/by-sa/4.0/';

    /**
     * Formats an internal snake_case permission_status identifier for human readability
     * using the active language translations.
     */
    function formatPermissionStatus(rawStatus) {
        if (!rawStatus || typeof rawStatus !== 'string') return '';
        const trimmed = rawStatus.trim();
        if (PERMISSION_STATUS_KEY_MAP[trimmed]) {
            return t(PERMISSION_STATUS_KEY_MAP[trimmed]);
        }
        return trimmed
            .split(/[_\s-]+/)
            .filter(Boolean)
            .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
            .join(' ');
    }

    /**
     * Appends required_attribution text to a container element, preserving exact text
     * and line breaks while turning embedded http(s) URLs (and "CC BY-SA 4.0" when
     * no Creative Commons URL is already present in a cc_by_sa attribution) into safe
     * external anchor elements.
     */
    function appendAttributionWithLinks(container, rawText, permissionStatus) {
        if (!container || typeof rawText !== 'string' || rawText === '') return;

        const hasExplicitCcUrl = /https?:\/\/creativecommons\.org\/licenses\/by-sa\/4\.0\/?/i.test(rawText);
        const tokenPattern = (permissionStatus === 'cc_by_sa' && !hasExplicitCcUrl)
            ? /(https?:\/\/[^\s]+|CC BY-SA 4\.0)/g
            : /(https?:\/\/[^\s]+)/g;

        let lastIndex = 0;
        let match;
        while ((match = tokenPattern.exec(rawText)) !== null) {
            if (match.index > lastIndex) {
                container.appendChild(document.createTextNode(rawText.slice(lastIndex, match.index)));
            }

            const matchedToken = match[0];
            if (matchedToken === 'CC BY-SA 4.0') {
                const ccLink = document.createElement('a');
                ccLink.className = 'scripture-attribution-link';
                ccLink.href = CC_BY_SA_4_URL;
                ccLink.target = '_blank';
                ccLink.rel = 'noopener noreferrer';
                ccLink.textContent = matchedToken;
                container.appendChild(ccLink);
            } else {
                const trailingMatch = matchedToken.match(/([.,;)]+)$/);
                const cleanUrl = trailingMatch
                    ? matchedToken.slice(0, matchedToken.length - trailingMatch[1].length)
                    : matchedToken;
                const trailingPunct = trailingMatch ? trailingMatch[1] : '';

                const urlLink = document.createElement('a');
                urlLink.className = 'scripture-attribution-link';
                urlLink.href = cleanUrl;
                urlLink.target = '_blank';
                urlLink.rel = 'noopener noreferrer';
                urlLink.textContent = cleanUrl;
                container.appendChild(urlLink);

                if (trailingPunct) {
                    container.appendChild(document.createTextNode(trailingPunct));
                }
            }

            lastIndex = tokenPattern.lastIndex;
        }

        if (lastIndex < rawText.length) {
            container.appendChild(document.createTextNode(rawText.slice(lastIndex)));
        }
    }

    /**
     * Creates a decorative 14px inline SVG external-link icon (square with outward arrow).
     */
    function createExternalLinkIcon() {
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', 'external-link-icon');
        svg.setAttribute('width', '14');
        svg.setAttribute('height', '14');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');

        const boxPath = document.createElementNS(svgNS, 'path');
        boxPath.setAttribute('d', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6');

        const poly = document.createElementNS(svgNS, 'polyline');
        poly.setAttribute('points', '15 3 21 3 21 9');

        const line = document.createElementNS(svgNS, 'line');
        line.setAttribute('x1', '10');
        line.setAttribute('y1', '14');
        line.setAttribute('x2', '21');
        line.setAttribute('y2', '3');

        svg.appendChild(boxPath);
        svg.appendChild(poly);
        svg.appendChild(line);
        return svg;
    }

    /**
     * Creates a decorative compact inline SVG icon for the Color Key.
     * Contains 3 horizontally arranged, rounded squares with BST category colors:
     * Red #990000 (History), Green #446644 (Poetry / Letters), Blue #003399 (Prophecy).
     */
    function createColorKeyIcon() {
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', 'color-key-icon');
        svg.setAttribute('width', '24');
        svg.setAttribute('height', '7');
        svg.setAttribute('viewBox', '0 0 24 7');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');

        const rects = [
            { x: '0', color: '#990000' },
            { x: '8.5', color: '#446644' },
            { x: '17', color: '#003399' }
        ];
        rects.forEach(({ x, color }) => {
            const rect = document.createElementNS(svgNS, 'rect');
            rect.setAttribute('x', x);
            rect.setAttribute('y', '0');
            rect.setAttribute('width', '7');
            rect.setAttribute('height', '7');
            rect.setAttribute('rx', '1.5');
            rect.setAttribute('fill', color);
            svg.appendChild(rect);
        });
        return svg;
    }

    /**
     * Creates a decorative compact inline SVG globe icon for the Language selector.
     * @returns {SVGSVGElement}
     */
    function createGlobeIcon() {
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', 'globe-icon');
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');

        const circle = document.createElementNS(svgNS, 'circle');
        circle.setAttribute('cx', '12');
        circle.setAttribute('cy', '12');
        circle.setAttribute('r', '10');
        svg.appendChild(circle);

        const path1 = document.createElementNS(svgNS, 'path');
        path1.setAttribute('d', 'M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20');
        svg.appendChild(path1);

        const path2 = document.createElementNS(svgNS, 'path');
        path2.setAttribute('d', 'M2 12h20');
        svg.appendChild(path2);

        return svg;
    }

    // Theme Management
    const THEME_STORAGE_KEY = 'bst_theme';

    /**
     * Resolves the current theme setting:
     * 1. Stored manual preference (if valid 'dark' or 'light')
     * 2. System preference via prefers-color-scheme
     * 3. Fallback to 'light'
     */
    function getResolvedTheme() {
        try {
            const stored = localStorage.getItem(THEME_STORAGE_KEY);
            if (stored === 'dark' || stored === 'light') {
                return stored;
            }
        } catch (_) {}

        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        return 'light';
    }

    /**
     * Creates an inline SVG element for the theme toggle icon.
     * @param {'moon'|'sun'} type
     * @returns {SVGSVGElement}
     */
    function createThemeIcon(type) {
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', `theme-toggle-icon icon-${type}`);
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');

        if (type === 'moon') {
            const path = document.createElementNS(svgNS, 'path');
            path.setAttribute('d', 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z');
            svg.appendChild(path);
        } else {
            const circle = document.createElementNS(svgNS, 'circle');
            circle.setAttribute('cx', '12');
            circle.setAttribute('cy', '12');
            circle.setAttribute('r', '4');
            svg.appendChild(circle);

            const rayCoords = [
                'M12 2v2', 'M12 20v2',
                'm4.93 4.93 1.41 1.41', 'm17.66 17.66 1.41 1.41',
                'M2 12h2', 'M20 12h2',
                'm6.34 17.66-1.41 1.41', 'm19.07 4.93-1.41 1.41'
            ];
            rayCoords.forEach(d => {
                const path = document.createElementNS(svgNS, 'path');
                path.setAttribute('d', d);
                svg.appendChild(path);
            });
        }
        return svg;
    }

    /**
     * Applies the theme to the document and updates the theme toggle button.
     * @param {'dark'|'light'} theme
     * @param {boolean} [persist=false] Whether this is a manual change to save
     */
    function applyTheme(theme, persist = false) {
        const effectiveTheme = (theme === 'dark') ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', effectiveTheme);

        if (persist) {
            try {
                localStorage.setItem(THEME_STORAGE_KEY, effectiveTheme);
            } catch (_) {}
        }

        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.innerHTML = '';
            // In light mode, icon is Moon (action: switch to dark)
            // In dark mode, icon is Sun (action: switch to light)
            const iconType = (effectiveTheme === 'dark') ? 'sun' : 'moon';
            const nextLabel = (effectiveTheme === 'dark') ? t('switch_to_light') : t('switch_to_dark');

            themeToggle.setAttribute('aria-label', nextLabel);
            themeToggle.removeAttribute('title'); // Explicit requirement: no hover tooltip

            themeToggle.appendChild(createThemeIcon(iconType));

            const srSpan = document.createElement('span');
            srSpan.className = 'visually-hidden';
            srSpan.textContent = nextLabel;
            themeToggle.appendChild(srSpan);
        }
    }

    /**
     * Updates the accessible label and screen-reader text of the theme toggle
     * without changing the current theme state.
     */
    function updateThemeToggleLabels() {
        const themeToggle = document.getElementById('theme-toggle');
        if (!themeToggle) return;
        const current = document.documentElement.getAttribute('data-theme') || 'light';
        const nextLabel = (current === 'dark') ? t('switch_to_light') : t('switch_to_dark');
        themeToggle.setAttribute('aria-label', nextLabel);
        const srSpan = themeToggle.querySelector('.visually-hidden');
        if (srSpan) {
            srSpan.textContent = nextLabel;
        }
    }

    function initThemeController() {
        const theme = getResolvedTheme();
        applyTheme(theme, false);

        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => {
                const current = document.documentElement.getAttribute('data-theme') || 'light';
                const next = (current === 'dark') ? 'light' : 'dark';
                applyTheme(next, true);
            });
        }

        // Listen for OS scheme changes when user hasn't set an explicit preference
        if (window.matchMedia) {
            try {
                const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
                const handleOsThemeChange = (e) => {
                    try {
                        const stored = localStorage.getItem(THEME_STORAGE_KEY);
                        if (!stored) {
                            applyTheme(e.matches ? 'dark' : 'light', false);
                        }
                    } catch (_) {}
                };
                if (mediaQuery.addEventListener) {
                    mediaQuery.addEventListener('change', handleOsThemeChange);
                } else if (mediaQuery.addListener) {
                    mediaQuery.addListener(handleOsThemeChange);
                }
            } catch (_) {}
        }
    }

    /**
     * Format a language code for compact button display.
     * Returns uppercase code, with Chinese variants distinguished:
     * - zh-CN -> ZH-CN
     * - zh-TW -> ZH-TW
     * - zh-pinyin -> ZH-PY
     *
     * @param {string} langCode
     * @returns {string}
     */
    function formatLanguageCode(langCode) {
        if (!langCode) return 'EN';
        const code = String(langCode).trim();
        if (code.toLowerCase() === 'zh-pinyin') {
            return 'ZH-PY';
        }
        return code.toUpperCase();
    }

    /**
     * Update the visible text inside the compact language selector button.
     *
     * @param {string} langCode
     */
    function updateSelectedLanguageDisplay(langCode) {
        const displayEl = document.getElementById('selected-lang-display');
        if (displayEl) {
            displayEl.textContent = formatLanguageCode(langCode);
        }
    }

    /**
     * Creates a decorative 16px inline SVG eye icon for Show/Hide toggle buttons.
     * - When isExpanded === false (content hidden), renders an open eye icon (action: Show).
     * - When isExpanded === true (content visible), renders an eye-with-slash icon (action: Hide).
     */
    function createToggleEyeIcon(isExpanded) {
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', `toggle-eye-icon ${isExpanded ? 'icon-hide' : 'icon-show'}`);
        svg.setAttribute('data-icon-state', isExpanded ? 'hide' : 'show');
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');

        if (isExpanded) {
            const path = document.createElementNS(svgNS, 'path');
            path.setAttribute(
                'd',
                'M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24'
            );
            const slash = document.createElementNS(svgNS, 'line');
            slash.setAttribute('x1', '1');
            slash.setAttribute('y1', '1');
            slash.setAttribute('x2', '23');
            slash.setAttribute('y2', '23');
            svg.appendChild(path);
            svg.appendChild(slash);
        } else {
            const outline = document.createElementNS(svgNS, 'path');
            outline.setAttribute('d', 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z');
            const pupil = document.createElementNS(svgNS, 'circle');
            pupil.setAttribute('cx', '12');
            pupil.setAttribute('cy', '12');
            pupil.setAttribute('r', '3');
            svg.appendChild(outline);
            svg.appendChild(pupil);
        }

        return svg;
    }

    /**
     * Updates a Show/Hide icon button (.toggle-scripture-btn or .toggle-references-btn)
     * with the appropriate eye/eye-slash SVG, aria-expanded, aria-label, and title.
     */
    function updateToggleIconButton(btn, isExpanded, targetLabel) {
        if (!btn) return;
        const actionVerb = isExpanded ? t('action_hide') : t('action_show');
        const accessibleLabel = `${actionVerb} ${targetLabel}`;
        btn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        btn.setAttribute('aria-label', accessibleLabel);
        btn.setAttribute('title', accessibleLabel);
        btn.replaceChildren(createToggleEyeIcon(isExpanded));
    }

    const INFO_ICON_SVG_HTML = '<svg class="toggle-info-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';

    /**
     * Escapes a string for safe interpolation inside HTML attribute values.
     */
    function escapeAttr(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    /**
     * Renders the HTML string for a Bible book card's circular "i" About toggle button.
     */
    function renderAboutToggleButtonHtml(nodeId, bookName, isExpanded, panelId) {
        const accessibleLabel = isExpanded
            ? t('hide_book_info', { book: bookName })
            : t('show_book_info', { book: bookName });
        const activeClass = isExpanded ? ' active' : '';
        const ariaExpanded = isExpanded ? 'true' : 'false';
        return `
            <button type="button"
                    class="toggle-about-btn${activeClass}"
                    data-node-id="${escapeAttr(nodeId)}"
                    data-book-name="${escapeAttr(bookName)}"
                    aria-expanded="${ariaExpanded}"
                    aria-controls="${escapeAttr(panelId)}"
                    aria-label="${escapeAttr(accessibleLabel)}"
                    title="${escapeAttr(accessibleLabel)}">${INFO_ICON_SVG_HTML}</button>
        `;
    }

    /**
     * Updates a Bible book card's circular "i" About toggle button (.toggle-about-btn)
     * with aria-expanded, aria-label, title, and active styling while keeping the icon unchanged.
     */
    function updateAboutToggleButton(btn, isExpanded, bookName) {
        if (!btn) return;
        const accessibleLabel = isExpanded
            ? t('hide_book_info', { book: bookName })
            : t('show_book_info', { book: bookName });
        btn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        btn.setAttribute('aria-label', accessibleLabel);
        btn.setAttribute('title', accessibleLabel);
        btn.classList.toggle('active', Boolean(isExpanded));
        if (!btn.querySelector('svg.toggle-info-icon')) {
            btn.innerHTML = INFO_ICON_SVG_HTML;
        }
    }

    const PEOPLE_EVENTS_ICONS_SVG_HTML =
        '<svg class="toggle-chips-icon toggle-people-icon" data-icon-role="people" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="7" r="3"></circle><path d="M7 20v-1a5 5 0 0 1 10 0v1"></path><circle cx="5.5" cy="9.5" r="2"></circle><path d="M2 20v-.5a3.5 3.5 0 0 1 3.5-3.5"></path><circle cx="18.5" cy="9.5" r="2"></circle><path d="M22 20v-.5a3.5 3.5 0 0 0-3.5-3.5"></path></svg>' +
        '<svg class="toggle-chips-icon toggle-calendar-icon" data-icon-role="events" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line><path d="M8 14h.01"></path><path d="M12 14h.01"></path><path d="M16 14h.01"></path><path d="M8 18h.01"></path><path d="M12 18h.01"></path><path d="M16 18h.01"></path></svg>';

    /**
     * Renders the HTML string for a People & Events toggle button (.toggle-chips-btn)
     * containing side-by-side People (users-round) and Events (calendar-days) SVG icons.
     */
    function renderChipsToggleButtonHtml(nodeId, targetName, isExpanded, panelId) {
        const accessibleLabel = isExpanded
            ? t('hide_book_events', { book: targetName })
            : t('show_book_events', { book: targetName });
        const activeClass = isExpanded ? ' active' : '';
        const ariaExpanded = isExpanded ? 'true' : 'false';
        return `
            <button type="button"
                    class="toggle-chips-btn${activeClass}"
                    data-node-id="${escapeAttr(nodeId)}"
                    data-target-name="${escapeAttr(targetName)}"
                    aria-expanded="${ariaExpanded}"
                    aria-controls="${escapeAttr(panelId)}"
                    aria-label="${escapeAttr(accessibleLabel)}"
                    title="${escapeAttr(accessibleLabel)}">${PEOPLE_EVENTS_ICONS_SVG_HTML}</button>
        `;
    }

    /**
     * Updates a People & Events toggle button (.toggle-chips-btn) with aria-expanded,
     * aria-label, title, and active styling while keeping both icons unchanged.
     */
    function updateChipsToggleButton(btn, isExpanded, targetName) {
        if (!btn) return;
        const accessibleLabel = isExpanded
            ? t('hide_book_events', { book: targetName })
            : t('show_book_events', { book: targetName });
        btn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        btn.setAttribute('aria-label', accessibleLabel);
        btn.setAttribute('title', accessibleLabel);
        btn.classList.toggle('active', Boolean(isExpanded));
        if (btn.querySelectorAll('svg.toggle-chips-icon').length !== 2) {
            btn.innerHTML = PEOPLE_EVENTS_ICONS_SVG_HTML;
        }
    }

    /**
     * Sanitizes an identifier for safe use in DOM element IDs and aria-controls attributes.
     */
    function sanitizeId(value) {
        return String(value).replace(/[^a-zA-Z0-9_-]/g, '-');
    }

    /**
     * Appends a child element to a parent only if the child is non-null.
     */
    function appendIfPresent(parent, child) {
        if (parent && child) {
            parent.appendChild(child);
        }
    }

    // Set up click delegator for showing/hiding attached chips (people/events) and about descriptions
    const spineContainerElement = document.getElementById('bible-layout-spine');
    if (spineContainerElement) {
        spineContainerElement.addEventListener('click', (event) => {
            const btn = event.target.closest('.toggle-chips-btn');
            if (btn) {
                const nodeId = btn.getAttribute('data-node-id');
                if (!nodeId) return;

                // Toggle node expansion state
                const isExpanded = expandedNodes.has(nodeId);
                if (isExpanded) {
                    expandedNodes.delete(nodeId);
                } else {
                    expandedNodes.add(nodeId);
                }

                // Update UI element attributes and active styling
                const nextExpanded = !isExpanded;
                const targetName = btn.getAttribute('data-target-name') ||
                    btn.closest('.node-card')?.querySelector('.book-subtitle, .node-title')?.textContent?.trim() ||
                    nodeId;
                updateChipsToggleButton(btn, nextExpanded, targetName);

                const controlsId = btn.getAttribute('aria-controls') || `chips-area-${sanitizeId(nodeId)}`;
                const chipsArea = document.getElementById(controlsId);
                if (chipsArea) {
                    chipsArea.style.display = nextExpanded ? 'block' : 'none';
                }
                return;
            }

            const aboutBtn = event.target.closest('.toggle-about-btn');
            if (aboutBtn) {
                const nodeId = aboutBtn.getAttribute('data-node-id');
                if (!nodeId) return;

                // Toggle about expansion state
                const isExpanded = expandedAboutNodes.has(nodeId);
                if (isExpanded) {
                    expandedAboutNodes.delete(nodeId);
                } else {
                    expandedAboutNodes.add(nodeId);
                }

                // Update UI element attributes and active styling
                const nextExpanded = !isExpanded;
                const bookName = aboutBtn.getAttribute('data-book-name') ||
                    aboutBtn.closest('.node-card, .attached-book-card')?.querySelector('.book-subtitle, .attached-book-subtitle')?.textContent?.trim() ||
                    nodeId;
                updateAboutToggleButton(aboutBtn, nextExpanded, bookName);

                const controlsId = aboutBtn.getAttribute('aria-controls') || `about-area-${sanitizeId(nodeId)}`;
                const aboutArea = document.getElementById(controlsId) ||
                    aboutBtn.closest('.node-card, .attached-book-card')?.querySelector('.attached-about-area');
                if (aboutArea) {
                    aboutArea.style.display = nextExpanded ? 'block' : 'none';
                }
                return;
            }

            const scriptureBtn = event.target.closest('.toggle-scripture-btn');
            if (scriptureBtn) {
                const passageId = scriptureBtn.getAttribute('data-passage-id');
                if (!passageId) return;

                const isExpanded = expandedScripturePassages.has(passageId);
                if (isExpanded) {
                    expandedScripturePassages.delete(passageId);
                } else {
                    expandedScripturePassages.add(passageId);
                }

                const nextExpanded = !isExpanded;
                const referenceLabel = scriptureBtn.getAttribute('data-reference-label') ||
                    scriptureBtn.closest('.creation-scripture-row, .closing-scripture-row')?.querySelector('.creation-scripture-ref, .closing-scripture-ref')?.textContent?.trim() ||
                    passageId;
                updateToggleIconButton(scriptureBtn, nextExpanded, referenceLabel);

                const controlsId = scriptureBtn.getAttribute('aria-controls') || `scripture-verse-${sanitizeId(passageId)}`;
                const verseArea = document.getElementById(controlsId);
                if (verseArea) {
                    verseArea.style.display = nextExpanded ? 'block' : 'none';
                }
                return;
            }

            const referencesBtn = event.target.closest('.toggle-references-btn');
            if (referencesBtn) {
                isReferencesExpanded = !isReferencesExpanded;
                updateToggleIconButton(referencesBtn, isReferencesExpanded, t('references_and_permissions'));

                const controlsId = referencesBtn.getAttribute('aria-controls') || 'references-permissions-panel';
                const panel = document.getElementById(controlsId);
                if (panel) {
                    panel.style.display = isReferencesExpanded ? 'block' : 'none';
                }
                return;
            }

            const scripPermBtn = event.target.closest('.toggle-scripture-permissions-btn');
            if (scripPermBtn) {
                isScripturePermissionsExpanded = !isScripturePermissionsExpanded;
                updateToggleIconButton(scripPermBtn, isScripturePermissionsExpanded, t('scripture_sources_and_permissions'));

                const controlsId = scripPermBtn.getAttribute('aria-controls') || 'scripture-permissions-panel';
                const panel = document.getElementById(controlsId);
                if (panel) {
                    panel.style.display = isScripturePermissionsExpanded ? 'block' : 'none';
                }
                return;
            }
        });
    }

    // Set up responsive Color Key legend controller (desktop >=992px floating vs mobile <992px collapsible panel)
    const colorKeyToggle = document.getElementById('color-key-toggle');
    const colorKeyLegend = document.getElementById('color-key-legend');
    const colorKeyClose = document.getElementById('color-key-close');

    if (colorKeyToggle && colorKeyLegend && colorKeyClose) {
        const desktopMediaQuery = window.matchMedia('(min-width: 992px)');
        const isDesktopViewport = () => desktopMediaQuery.matches;
        let desktopLegendExpanded = true;
        let mobileLegendExpanded = false;
        let lastIsDesktop = isDesktopViewport();

        const applyLegendState = (isExpanded, { scrollIntoViewOnMobile = false } = {}) => {
            const expanded = Boolean(isExpanded);
            if (expanded) {
                colorKeyLegend.classList.remove('hidden');
                colorKeyToggle.setAttribute('aria-expanded', 'true');
                colorKeyToggle.classList.add('active');
                colorKeyClose.setAttribute('aria-label', t('hide_color_key'));

                if (scrollIntoViewOnMobile && !isDesktopViewport()) {
                    const headerEl = document.querySelector('.sticky-header');
                    const headerHeight = headerEl ? headerEl.getBoundingClientRect().height : 0;
                    colorKeyLegend.style.scrollMarginTop = `${Math.ceil(headerHeight + 12)}px`;
                    const legendRect = colorKeyLegend.getBoundingClientRect();
                    if (legendRect.top < headerHeight || legendRect.bottom > window.innerHeight) {
                        const docTop = window.scrollY + legendRect.top;
                        const targetScrollY = Math.max(0, Math.round(docTop - headerHeight - 12));
                        window.scrollTo(0, targetScrollY);
                    }
                }
            } else {
                colorKeyLegend.classList.add('hidden');
                colorKeyToggle.setAttribute('aria-expanded', 'false');
                colorKeyToggle.classList.remove('active');
                colorKeyClose.setAttribute('aria-label', t('show_color_key'));
            }
        };

        const toggleLegend = (forceState, { restoreFocusToToggle = false } = {}) => {
            const isCurrentlyHidden = colorKeyLegend.classList.contains('hidden');
            const nextExpanded = forceState !== undefined ? Boolean(forceState) : isCurrentlyHidden;

            if (isDesktopViewport()) {
                desktopLegendExpanded = nextExpanded;
            } else {
                mobileLegendExpanded = nextExpanded;
            }

            applyLegendState(nextExpanded, { scrollIntoViewOnMobile: nextExpanded });

            if (!nextExpanded && restoreFocusToToggle) {
                colorKeyToggle.focus({ preventScroll: true });
            }
        };

        const syncLegendViewportMode = () => {
            const nowDesktop = isDesktopViewport();
            if (nowDesktop !== lastIsDesktop) {
                lastIsDesktop = nowDesktop;
                if (nowDesktop) {
                    applyLegendState(desktopLegendExpanded, { scrollIntoViewOnMobile: false });
                } else {
                    mobileLegendExpanded = false;
                    applyLegendState(false, { scrollIntoViewOnMobile: false });
                }
            }
        };

        // Initialize legend state for the current viewport width
        applyLegendState(lastIsDesktop ? desktopLegendExpanded : mobileLegendExpanded, {
            scrollIntoViewOnMobile: false
        });

        if (typeof desktopMediaQuery.addEventListener === 'function') {
            desktopMediaQuery.addEventListener('change', syncLegendViewportMode);
        }
        window.addEventListener('resize', syncLegendViewportMode);

        colorKeyToggle.addEventListener('click', () => {
            toggleLegend();
        });

        colorKeyClose.addEventListener('click', () => {
            toggleLegend(false, { restoreFocusToToggle: true });
        });
    }

    // Initialize Theme Controller (Light/Dark Mode)
    initThemeController();

    // Fetch website dataset and UI translations concurrently
    Promise.all([
        fetch(JSON_PATH).then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        }),
        fetch(UI_TRANSLATIONS_PATH)
            .then(response => response.ok ? response.json() : {})
            .catch(error => {
                console.warn('Could not load UI translations; using English fallback.', error);
                return {};
            })
    ])
    .then(([data, uiData]) => {
        bstData = data;
        uiTranslations = uiData || {};
        
        // Remove loading placeholder
        const loadingMsg = document.getElementById('timeline-loading-message');
        if (loadingMsg) {
            loadingMsg.remove();
        }

        // Populate languages dropdown
        initializeLanguageSelector();

        // Set default language
        currentLang = bstData.translations && bstData.translations.en ? 'en' : Object.keys(bstData.translations)[0];
        langSelect.value = currentLang;
        updateSelectedLanguageDisplay(currentLang);
        langSelect.removeAttribute('disabled');

        // Render view
        renderActiveLanguage();
    })
    .catch(error => {
        console.error('Error loading BST data:', error);
        const loadingMsg = document.getElementById('timeline-loading-message');
        if (loadingMsg) {
            loadingMsg.textContent = t('error_loading_timeline');
            loadingMsg.className = 'placeholder-text error-message';
        }
    });

    /**
     * Parse available languages from translations map and fill the select dropdown.
     */
    function initializeLanguageSelector() {
        if (!bstData.translations) return;
        
        langSelect.innerHTML = '';
        const entries = Object.entries(bstData.translations);
        // Ensure English remains the first dropdown option
        entries.sort(([codeA], [codeB]) => {
            if (codeA === 'en') return -1;
            if (codeB === 'en') return 1;
            return 0;
        });

        entries.forEach(([langCode, langInfo]) => {
            const opt = document.createElement('option');
            opt.value = langCode;
            opt.textContent = `${langInfo.language_name || langCode} (${langCode})`;
            langSelect.appendChild(opt);
        });

        // Add event listener for selection change
        langSelect.addEventListener('change', (e) => {
            currentLang = e.target.value;
            updateSelectedLanguageDisplay(currentLang);
            renderActiveLanguage();
        });
    }

    /**
     * Updates the main prototype header title, subtitle, and original graphic link
     * with localized strings for the active language.
     */
    function updateHeaderTranslations(currentLangData) {
        const enTrans = (bstData.translations && bstData.translations.en) || {};
        const langStrings = (currentLangData && currentLangData.strings) || {};
        const enStrings = enTrans.strings || {};

        // 1. Header Title: reuse existing string_id "bible_structure_and_timeline"
        const titleEl = document.getElementById('timeline-main-heading');
        if (titleEl) {
            const rawTitle = langStrings.bible_structure_and_timeline;
            titleEl.textContent = (rawTitle !== undefined && rawTitle !== null && String(rawTitle).trim() !== '')
                ? String(rawTitle)
                : (enStrings.bible_structure_and_timeline || 'Bible Structure and Timeline');
        }

        // 2. Header Subtitle: string_id "timeline_subtitle"
        const descEl = document.querySelector('.layout-prototype-header .layout-desc');
        if (descEl) {
            const rawSubtitle = langStrings.timeline_subtitle;
            descEl.textContent = (rawSubtitle !== undefined && rawSubtitle !== null && String(rawSubtitle).trim() !== '')
                ? String(rawSubtitle)
                : (enStrings.timeline_subtitle || 'A visual guide that illustrates the structure of the Bible, arranged chronologically.');
        }

        // 3. Original Graphic Link: string_id "header_original_graphic_link"
        const linkEl = document.getElementById('header-original-graphic-link');
        if (linkEl) {
            const rawLinkText = langStrings.header_original_graphic_link;
            const linkText = (rawLinkText !== undefined && rawLinkText !== null && String(rawLinkText).trim() !== '')
                ? String(rawLinkText)
                : (enStrings.header_original_graphic_link || 'An interactive version of the original BST graphic');

            linkEl.href = 'https://basicsofthebible.org/bst/';
            linkEl.target = '_blank';
            linkEl.rel = 'noopener noreferrer';
            linkEl.setAttribute('aria-label', `${linkText} ${t('opens_in_new_tab')}`);
            linkEl.innerHTML = '';

            const textSpan = document.createElement('span');
            textSpan.className = 'header-original-graphic-link-text';
            textSpan.textContent = linkText;
            linkEl.appendChild(textSpan);
            linkEl.appendChild(createExternalLinkIcon());
        }
    }

    /**
     * Updates static interface controls, accessibility attributes, the color-key legend,
     * and the browser document title for the active language.
     */
    function updateStaticInterfaceTranslations(langInfo) {
        const enTrans = (bstData.translations && bstData.translations.en) || {};
        const langStrings = (langInfo && langInfo.strings) || {};
        const enStrings = enTrans.strings || {};

        // 1. Browser document title: localized master-data title + site brand
        const rawTitle = langStrings.bible_structure_and_timeline;
        const localizedTitle = (rawTitle !== undefined && rawTitle !== null && String(rawTitle).trim() !== '')
            ? String(rawTitle)
            : (enStrings.bible_structure_and_timeline || 'Bible Structure and Timeline');
        document.title = `${localizedTitle} • basicsofthebible.org`;

        // 2. Language selector accessibility labels
        const langSelectLabel = document.querySelector('label[for="language-select"]');
        if (langSelectLabel) {
            langSelectLabel.textContent = t('select_language');
        }
        if (langSelect) {
            langSelect.setAttribute('aria-label', t('select_language'));
            langSelect.setAttribute('title', t('select_language'));
        }

        // 3. Theme toggle button labels
        updateThemeToggleLabels();

        // 4. Color Key button, heading, and close button
        const colorKeyToggle = document.getElementById('color-key-toggle');
        if (colorKeyToggle) {
            colorKeyToggle.setAttribute('aria-label', t('color_key'));
            colorKeyToggle.setAttribute('title', t('color_key'));
            const srSpan = colorKeyToggle.querySelector('.visually-hidden');
            if (srSpan) {
                srSpan.textContent = t('color_key');
            }
        }

        const colorKeyHeading = document.getElementById('color-key-heading');
        if (colorKeyHeading) {
            const srSpan = colorKeyHeading.querySelector('.visually-hidden');
            if (srSpan) {
                srSpan.textContent = t('color_key');
            }
        }

        const colorKeyClose = document.getElementById('color-key-close');
        if (colorKeyClose) {
            const isCurrentlyHidden = colorKeyLegend ? colorKeyLegend.classList.contains('hidden') : false;
            colorKeyClose.setAttribute('aria-label', isCurrentlyHidden ? t('show_color_key') : t('hide_color_key'));
        }

        // 5. Color Key legend items:
        // History: master data translation
        const swatchHist = document.querySelector('#color-key-legend .swatch-history');
        if (swatchHist && swatchHist.nextElementSibling) {
            const rawHist = langStrings.history;
            swatchHist.nextElementSibling.textContent = (rawHist !== undefined && rawHist !== null && String(rawHist).trim() !== '')
                ? String(rawHist)
                : (enStrings.history || 'History');
        }
        // Prophecy: master data translation
        const swatchProphecy = document.querySelector('#color-key-legend .swatch-prophecy');
        if (swatchProphecy && swatchProphecy.nextElementSibling) {
            const rawProphecy = langStrings.prophecy;
            swatchProphecy.nextElementSibling.textContent = (rawProphecy !== undefined && rawProphecy !== null && String(rawProphecy).trim() !== '')
                ? String(rawProphecy)
                : (enStrings.prophecy || 'Prophecy');
        }
        // Docked compound: Poetry (OT) / Letters (NT)
        const dockedPoetryLetters = document.querySelector('#color-key-legend .legend-item-docked-only .legend-label');
        if (dockedPoetryLetters) {
            dockedPoetryLetters.textContent = t('legend_poetry_letters');
        }
        // Floating items: Poetry (OT), Letters (NT)
        const floatingLabels = document.querySelectorAll('#color-key-legend .legend-item-floating-only .legend-label');
        if (floatingLabels.length >= 2) {
            floatingLabels[0].textContent = t('legend_poetry_ot');
            floatingLabels[1].textContent = t('legend_letters_nt');
        }
    }

    /**
     * Handles translations for book/string nodes, sorting, grouping, and rendering everything.
     */
    function renderActiveLanguage() {
        if (!bstData) return;

        // Keep compact language selector button code in sync
        updateSelectedLanguageDisplay(currentLang);

        const langInfo = bstData.translations[currentLang] || {};
        const isRtl = langInfo.text_direction === 'rtl';

        // Apply page-wide language and directionality for modern logical layout rules
        document.documentElement.lang = currentLang;
        document.documentElement.dir = isRtl ? 'rtl' : 'ltr';
        document.body.dir = isRtl ? 'rtl' : 'ltr';

        // Localize main webpage header title, subtitle, and original graphic link
        updateHeaderTranslations(langInfo);

        // Localize static interface controls, labels, and browser document title
        updateStaticInterfaceTranslations(langInfo);

        // Process nodes list
        const rawNodes = bstData.metadata.timeline_nodes || {};
        const nodesList = Object.entries(rawNodes).map(([nodeId, nodeObj]) => {
            return {
                node_id: nodeId,
                ...nodeObj
            };
        });

        // Render the continuous Bible layout representation
        renderContinuousTimeline(nodesList);
    }

    /**
     * Renders a continuous vertical layout of the Bible timeline:
     * OT primary -> Intertestamental Period -> NT primary,
     * attaching concurrent books, people, and event chips, and displaying timeline markers.
     */
    function renderContinuousTimeline(nodesList) {
        const spineContainer = document.getElementById('bible-layout-spine');
        if (!spineContainer) return;
        
        spineContainer.innerHTML = '';

        const langData = bstData.translations[currentLang] || {};

        // 0. Render Introductory Creation Pill & "In the beginning..." Card before 4000 BC
        renderCreationIntroduction(spineContainer, langData);

        // 1. Create common historical timeline wrapper and shared continuous vertical rail
        const historicalWrapper = document.createElement('div');
        historicalWrapper.id = 'historical-timeline-wrapper';
        historicalWrapper.className = 'historical-timeline-wrapper';

        const rail = document.createElement('div');
        rail.id = 'timeline-vertical-rail';
        rail.className = 'timeline-vertical-rail';
        rail.setAttribute('aria-hidden', 'true');
        historicalWrapper.appendChild(rail);

        spineContainer.appendChild(historicalWrapper);

        // 2. Retrieve and sort markers from lanes
        const otMarkers = getTimelineMarkers('ot_date_markers');
        const ntMarkers = getTimelineMarkers('nt_date_markers');
        const mainSpineMarkers = getTimelineMarkers('main_spine');

        // 3. Render OT Markers & associated nodes
        otMarkers.forEach(m => {
            // Render marker break
            appendIfPresent(historicalWrapper, renderTimelineMarker(m));

            // Find non-transition primary nodes that resolve to this marker
            const primaryNodes = nodesList.filter(p => 
                p.timeline_id === 'ot_primary' &&
                p.timeline_role === 'primary_sequence' &&
                getMarkerAnchorId(p.node_id, nodesList) === m.node_id
            ).sort(compareNodes);

            // Render primary nodes
            primaryNodes.forEach(p => {
                // Find attached secondary books (anchored directly to primary node)
                const attachedBooks = nodesList.filter(s => 
                    s.timeline_id === 'ot_secondary' &&
                    s.anchor_node_id === p.node_id
                ).sort(compareNodes);

                // Find attached chips (people/events)
                const pSecBookIds = new Set(attachedBooks.map(s => s.node_id));
                const attachedChips = nodesList.filter(pe => 
                    (pe.node_type === 'person' || pe.node_type === 'event') &&
                    (pe.anchor_node_id === p.node_id || pe.secondary_anchor_node_id === p.node_id || pSecBookIds.has(pe.anchor_node_id))
                ).sort(compareNodes);

                if ((p.timeline_role === 'transition_period' || p.node_type === 'timeline_break') && attachedBooks.length === 0 && attachedChips.length === 0) {
                    historicalWrapper.appendChild(renderTransitionPeriod(p));
                } else {
                    renderSpineRow(p, attachedBooks, attachedChips, false, null);
                }
            });

            // Find OT secondary books anchored directly to this marker
            const markerSecondaryBooks = nodesList.filter(s =>
                s.timeline_id === 'ot_secondary' &&
                s.anchor_node_id === m.node_id
            ).sort(compareNodes);

            if (markerSecondaryBooks.length > 0) {
                renderSpineRowForSecondaryOnly(markerSecondaryBooks);
            }

            // Find transition period primary nodes that resolve to this marker
            const transitionNodes = nodesList.filter(p => 
                p.timeline_id === 'ot_primary' &&
                p.timeline_role === 'transition_period' &&
                getMarkerAnchorId(p.node_id, nodesList) === m.node_id
            ).sort(compareNodes);

            // Render transition nodes
            transitionNodes.forEach(p => {
                // Find attached secondary books (anchored directly to primary node)
                const attachedBooks = nodesList.filter(s => 
                    s.timeline_id === 'ot_secondary' &&
                    s.anchor_node_id === p.node_id
                ).sort(compareNodes);

                // Find attached chips (people/events)
                const pSecBookIds = new Set(attachedBooks.map(s => s.node_id));
                const attachedChips = nodesList.filter(pe => 
                    (pe.node_type === 'person' || pe.node_type === 'event') &&
                    (pe.anchor_node_id === p.node_id || pe.secondary_anchor_node_id === p.node_id || pSecBookIds.has(pe.anchor_node_id))
                ).sort(compareNodes);

                if (p.node_id === 'node_babylonian_exile') {
                    // Create and append the gold date-span pill for the exile
                    const exileMarker = {
                        node_id: 'node_babylonian_exile_date_marker',
                        reference_id: 'date_608_538_bc',
                        node_type: 'time_marker'
                    };
                    appendIfPresent(historicalWrapper, renderTimelineMarker(exileMarker));
                }

                if ((p.timeline_role === 'transition_period' || p.node_type === 'timeline_break') && attachedBooks.length === 0 && attachedChips.length === 0) {
                    historicalWrapper.appendChild(renderTransitionPeriod(p));
                } else {
                    renderSpineRow(p, attachedBooks, attachedChips, false, null);
                }
            });
        });

        // 3. Render NT Markers, Spine Markers & associated nodes (suppressing Present and Future date markers)
        const ntAndSpineMarkers = [...ntMarkers, ...mainSpineMarkers].filter(m =>
            m.node_id !== 'marker_present' &&
            m.node_id !== 'marker_future' &&
            m.reference_id !== 'date_present' &&
            m.reference_id !== 'date_future'
        );
        ntAndSpineMarkers.forEach(m => {
            // Render marker break
            appendIfPresent(historicalWrapper, renderTimelineMarker(m));

            // Find non-transition primary nodes that resolve to this marker
            const primaryNodes = nodesList.filter(p => 
                p.timeline_id === 'nt_primary' &&
                p.timeline_role === 'primary_sequence' &&
                getMarkerAnchorId(p.node_id, nodesList) === m.node_id
            ).sort(compareNodes);

            // Render primary nodes
            primaryNodes.forEach(p => {
                // Find attached secondary books (anchored directly to primary node)
                const attachedBooks = nodesList.filter(s => 
                    s.timeline_id === 'nt_secondary' &&
                    s.anchor_node_id === p.node_id
                ).sort(compareNodes);

                // Find attached chips (people/events)
                const pSecBookIds = new Set(attachedBooks.map(s => s.node_id));
                const attachedChips = nodesList.filter(pe => 
                    (pe.node_type === 'person' || pe.node_type === 'event') &&
                    (pe.anchor_node_id === p.node_id || pe.secondary_anchor_node_id === p.node_id || pSecBookIds.has(pe.anchor_node_id))
                ).sort(compareNodes);

                if ((p.timeline_role === 'transition_period' || p.node_type === 'timeline_break') && attachedBooks.length === 0 && attachedChips.length === 0) {
                    historicalWrapper.appendChild(renderTransitionPeriod(p));
                } else {
                    renderSpineRow(p, attachedBooks, attachedChips, false, null);
                }
            });

            // Find NT secondary books anchored directly to this marker
            const markerSecondaryBooks = nodesList.filter(s =>
                s.timeline_id === 'nt_secondary' &&
                s.anchor_node_id === m.node_id
            ).sort(compareNodes);

            // Find transition period primary nodes that resolve to this marker
            const transitionNodes = nodesList.filter(p => 
                p.timeline_id === 'nt_primary' &&
                p.timeline_role === 'transition_period' &&
                getMarkerAnchorId(p.node_id, nodesList) === m.node_id
            ).sort(compareNodes);

            if (markerSecondaryBooks.length > 0) {
                renderSpineRowForSecondaryOnly(markerSecondaryBooks);
            }

            // Render transition nodes
            transitionNodes.forEach(p => {
                // Find attached secondary books (anchored directly to primary node)
                const attachedBooks = nodesList.filter(s => 
                    s.timeline_id === 'nt_secondary' &&
                    s.anchor_node_id === p.node_id
                ).sort(compareNodes);

                // Find attached chips (people/events)
                const pSecBookIds = new Set(attachedBooks.map(s => s.node_id));
                const attachedChips = nodesList.filter(pe => 
                    (pe.node_type === 'person' || pe.node_type === 'event') &&
                    (pe.anchor_node_id === p.node_id || pe.secondary_anchor_node_id === p.node_id || pSecBookIds.has(pe.anchor_node_id))
                ).sort(compareNodes);

                if ((p.timeline_role === 'transition_period' || p.node_type === 'timeline_break') && attachedBooks.length === 0 && attachedChips.length === 0) {
                    historicalWrapper.appendChild(renderTransitionPeriod(p));
                } else {
                    renderSpineRow(p, attachedBooks, attachedChips, false, null);
                }
            });
        });

        // Align rail top exactly with the center of the first historical marker's gold dot
        const firstDot = historicalWrapper.querySelector('.timeline-marker-dot');
        if (firstDot) {
            const hwRect = historicalWrapper.getBoundingClientRect();
            const dotRect = firstDot.getBoundingClientRect();
            const dotCenterY = (dotRect.top + dotRect.height / 2) - hwRect.top;
            historicalWrapper.style.setProperty('--timeline-rail-top', `${dotCenterY}px`);
        }

        // 4. Render Closing Scripture Section ("What Is and What Is to Come...")
        renderClosingScriptureSection(spineContainer, langData);

        // 5. Render collapsible References & Permissions section after the final timeline content
        renderReferencesAndPermissions(spineContainer, nodesList, langData);

        // Helper function to render the collapsible References & Permissions section
        function renderReferencesAndPermissions(targetContainer, allNodesList, currentLangData) {
            const enTrans = (bstData.translations && bstData.translations.en) || {};
            const panelId = 'references-permissions-panel';

            const outerRow = document.createElement('article');
            outerRow.className = 'layout-row references-permissions-row';

            const outerCard = document.createElement('div');
            outerCard.className = 'node-card role-primary_sequence references-permissions-card';

            // Header row with title and Show/Hide button
            const headerDiv = document.createElement('div');
            headerDiv.className = 'node-header references-permissions-header';

            const titleSpan = document.createElement('span');
            titleSpan.className = 'node-title references-permissions-title';
            titleSpan.textContent = t('references_and_permissions');

            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'toggle-references-btn';
            toggleBtn.setAttribute('aria-controls', panelId);
            updateToggleIconButton(toggleBtn, isReferencesExpanded, t('references_and_permissions'));

            headerDiv.appendChild(titleSpan);
            headerDiv.appendChild(toggleBtn);
            outerCard.appendChild(headerDiv);

            // Collapsible body panel
            const panelDiv = document.createElement('div');
            panelDiv.id = panelId;
            panelDiv.className = 'references-permissions-panel';
            panelDiv.style.display = isReferencesExpanded ? 'block' : 'none';

            // Group 1: Bibliographic References (deduplicated from source_note nodes)
            const bibGroup = document.createElement('div');
            bibGroup.className = 'references-group bibliographic-references-group';

            const bibHeading = document.createElement('h4');
            bibHeading.className = 'references-group-heading bibliographic-heading';
            const rawSourcesHeading = currentLangData.strings && currentLangData.strings.sources;
            bibHeading.textContent = (rawSourcesHeading !== undefined && rawSourcesHeading !== null && String(rawSourcesHeading).trim() !== '')
                ? String(rawSourcesHeading)
                : ((enTrans.strings && enTrans.strings.sources) || 'Sources:');
            bibGroup.appendChild(bibHeading);

            const sourceNoteNodes = allNodesList
                .filter(n => n.timeline_role === 'source_note' && n.reference_id)
                .sort((a, b) => {
                    const anchorPriorityA = a.anchor_node_id === 'node_genesis' ? 0 : 1;
                    const anchorPriorityB = b.anchor_node_id === 'node_genesis' ? 0 : 1;
                    if (anchorPriorityA !== anchorPriorityB) return anchorPriorityA - anchorPriorityB;
                    const sortA = (a.cluster_sort_order !== undefined && a.cluster_sort_order !== null) ? a.cluster_sort_order : 999;
                    const sortB = (b.cluster_sort_order !== undefined && b.cluster_sort_order !== null) ? b.cluster_sort_order : 999;
                    return sortA - sortB;
                });

            const seenRefIds = new Set();
            const uniqueRefIds = [];
            sourceNoteNodes.forEach(node => {
                if (!seenRefIds.has(node.reference_id)) {
                    seenRefIds.add(node.reference_id);
                    uniqueRefIds.push(node.reference_id);
                }
            });

            const bibList = document.createElement('ul');
            bibList.className = 'references-list bibliographic-list';

            uniqueRefIds.forEach(refId => {
                const rawRefText = currentLangData.strings && currentLangData.strings[refId];
                const localizedRefText = (rawRefText !== undefined && rawRefText !== null && String(rawRefText).trim() !== '')
                    ? String(rawRefText)
                    : ((enTrans.strings && enTrans.strings[refId]) ? String(enTrans.strings[refId]) : refId);

                const li = document.createElement('li');
                li.className = 'reference-item bibliographic-item';
                li.setAttribute('data-reference-id', refId);

                const targetUrl = SOURCE_NOTE_URLS[refId];
                if (targetUrl) {
                    const link = document.createElement('a');
                    link.className = 'reference-link';
                    link.href = targetUrl;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.setAttribute('aria-label', `${localizedRefText} ${t('opens_in_new_tab')}`);

                    const linkTextSpan = document.createElement('span');
                    linkTextSpan.className = 'reference-link-text';
                    linkTextSpan.textContent = localizedRefText;

                    link.appendChild(linkTextSpan);
                    link.appendChild(createExternalLinkIcon());
                    li.appendChild(link);
                } else {
                    const span = document.createElement('span');
                    span.className = 'reference-text';
                    span.textContent = localizedRefText;
                    li.appendChild(span);
                }

                bibList.appendChild(li);
            });

            bibGroup.appendChild(bibList);
            panelDiv.appendChild(bibGroup);

            // Group 2: Scripture Sources & Permissions (all verified Scripture versions across translations)
            const scripPanelId = 'scripture-permissions-panel';
            const scripGroup = document.createElement('div');
            scripGroup.className = 'references-group scripture-permissions-group';

            const scripHeaderDiv = document.createElement('div');
            scripHeaderDiv.className = 'scripture-permissions-header';

            const scripHeading = document.createElement('h4');
            scripHeading.className = 'references-group-heading scripture-permissions-heading';
            scripHeading.textContent = t('scripture_sources_and_permissions');

            const scripToggleBtn = document.createElement('button');
            scripToggleBtn.type = 'button';
            scripToggleBtn.className = 'toggle-scripture-permissions-btn';
            scripToggleBtn.setAttribute('aria-controls', scripPanelId);
            updateToggleIconButton(scripToggleBtn, isScripturePermissionsExpanded, t('scripture_sources_and_permissions'));

            scripHeaderDiv.appendChild(scripHeading);
            scripHeaderDiv.appendChild(scripToggleBtn);
            scripGroup.appendChild(scripHeaderDiv);

            const scripPanelDiv = document.createElement('div');
            scripPanelDiv.id = scripPanelId;
            scripPanelDiv.className = 'scripture-permissions-panel';
            scripPanelDiv.style.display = isScripturePermissionsExpanded ? 'block' : 'none';

            const passagesMetaMap = (bstData.metadata && bstData.metadata.scripture_passages) || {};
            const orderedPassageIds = Object.values(passagesMetaMap)
                .sort((a, b) => {
                    const seqA = (a.sequence_order !== undefined && a.sequence_order !== null) ? a.sequence_order : 0;
                    const seqB = (b.sequence_order !== undefined && b.sequence_order !== null) ? b.sequence_order : 0;
                    return seqA - seqB;
                })
                .map(p => p.passage_id);

            const seenVersionNames = new Set();
            const uniqueScriptureVersions = [];
            const allTranslations = (bstData.translations && typeof bstData.translations === 'object')
                ? Object.values(bstData.translations)
                : [];

            allTranslations.forEach(langEntry => {
                const scriptureMap = (langEntry && langEntry.scripture) || {};
                const passageKeys = orderedPassageIds.length > 0 ? orderedPassageIds : Object.keys(scriptureMap);

                passageKeys.forEach(pid => {
                    const rec = scriptureMap[pid];
                    if (!rec) return;
                    const hasVerseText = typeof rec.verse_text === 'string' && rec.verse_text.trim() !== '';
                    const isNonPending = Boolean(rec.review_status) && rec.review_status !== 'pending_translation';
                    const versionName = typeof rec.version_name === 'string' ? rec.version_name.trim() : '';

                    if (hasVerseText && isNonPending && versionName && !seenVersionNames.has(versionName)) {
                        seenVersionNames.add(versionName);
                        uniqueScriptureVersions.push({
                            version_name: versionName,
                            permission_status: typeof rec.permission_status === 'string' ? rec.permission_status.trim() : '',
                            required_attribution: typeof rec.required_attribution === 'string' ? rec.required_attribution.trim() : ''
                        });
                    }
                });
            });

            const scripList = document.createElement('ul');
            scripList.className = 'references-list scripture-permissions-list';

            uniqueScriptureVersions.forEach(ver => {
                const li = document.createElement('li');
                li.className = 'reference-item scripture-permission-item';

                const mainLine = document.createElement('div');
                mainLine.className = 'scripture-permission-main';

                const nameSpan = document.createElement('span');
                nameSpan.className = 'scripture-version-name';
                nameSpan.textContent = ver.version_name;
                mainLine.appendChild(nameSpan);

                if (ver.permission_status) {
                    const statusSpan = document.createElement('span');
                    statusSpan.className = 'scripture-permission-status';
                    statusSpan.setAttribute('data-permission-status', ver.permission_status);
                    statusSpan.textContent = formatPermissionStatus(ver.permission_status);
                    mainLine.appendChild(statusSpan);
                }

                li.appendChild(mainLine);

                if (ver.required_attribution) {
                    const attrPara = document.createElement('p');
                    attrPara.className = 'scripture-required-attribution';
                    appendAttributionWithLinks(attrPara, ver.required_attribution, ver.permission_status);
                    li.appendChild(attrPara);
                }

                scripList.appendChild(li);
            });

            scripPanelDiv.appendChild(scripList);
            scripGroup.appendChild(scripPanelDiv);
            panelDiv.appendChild(scripGroup);

            outerCard.appendChild(panelDiv);
            outerRow.appendChild(outerCard);
            targetContainer.appendChild(outerRow);
        }

        // Helper function to render the introductory "In the beginning..." card
        function renderCreationIntroduction(targetContainer, currentLangData) {
            const enTrans = (bstData.translations && bstData.translations.en) || {};

            // 1. Render introductory card headed by string_id: event_in_the_beginning
            const introRow = document.createElement('article');
            introRow.className = 'layout-row creation-intro-row';

            const introCard = document.createElement('div');
            introCard.className = 'node-card role-primary_sequence creation-intro-card';

            const headerDiv = document.createElement('div');
            headerDiv.className = 'node-header';

            const headingSpan = document.createElement('span');
            headingSpan.className = 'node-title creation-intro-title';
            const rawHeading = currentLangData.strings && currentLangData.strings.event_in_the_beginning;
            headingSpan.textContent = (rawHeading !== undefined && rawHeading !== null && String(rawHeading).trim() !== '')
                ? String(rawHeading)
                : ((enTrans.strings && enTrans.strings.event_in_the_beginning) || 'In the beginning...');
            headerDiv.appendChild(headingSpan);
            introCard.appendChild(headerDiv);

            // 3. Render Scripture references and independent Show/Hide verse panels
            const passagesMetaMap = (bstData.metadata && bstData.metadata.scripture_passages) || {};
            const CREATION_PASSAGE_IDS = ['genesis_1_1', 'john_1_1'];
            const orderedPassages = CREATION_PASSAGE_IDS
                .map(id => passagesMetaMap[id])
                .filter(Boolean)
                .sort((a, b) => {
                    const seqA = (a.sequence_order !== undefined && a.sequence_order !== null) ? a.sequence_order : 0;
                    const seqB = (b.sequence_order !== undefined && b.sequence_order !== null) ? b.sequence_order : 0;
                    return seqA - seqB;
                });

            if (orderedPassages.length > 0) {
                const listDiv = document.createElement('div');
                listDiv.className = 'creation-scripture-list';

                const langScriptureMap = currentLangData.scripture || {};

                orderedPassages.forEach(pMeta => {
                    const passageId = pMeta.passage_id;
                    const bookId = pMeta.book_id;
                    const bookTrans = (currentLangData.books && currentLangData.books[bookId]) ||
                        (enTrans.books && enTrans.books[bookId]) || {};
                    const bookFullName = bookTrans.name_full || bookId;
                    const verseRange = (pMeta.verse_end && pMeta.verse_end !== pMeta.verse_start)
                        ? `${pMeta.verse_start}-${pMeta.verse_end}`
                        : `${pMeta.verse_start}`;
                    const referenceLabel = `${bookFullName} ${pMeta.chapter}:${verseRange}`;

                    const langPassage = langScriptureMap[passageId] || {};
                    const verseText = typeof langPassage.verse_text === 'string' ? langPassage.verse_text : '';

                    const isExpanded = expandedScripturePassages.has(passageId);
                    const panelId = `scripture-verse-${sanitizeId(passageId)}`;

                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'creation-scripture-item';
                    itemDiv.setAttribute('data-passage-id', passageId);

                    const refRow = document.createElement('div');
                    refRow.className = 'creation-scripture-row';

                    const refSpan = document.createElement('span');
                    refSpan.className = 'creation-scripture-ref';
                    refSpan.textContent = referenceLabel;

                    const toggleBtn = document.createElement('button');
                    toggleBtn.type = 'button';
                    toggleBtn.className = 'toggle-scripture-btn';
                    toggleBtn.setAttribute('data-passage-id', passageId);
                    toggleBtn.setAttribute('data-reference-label', referenceLabel);
                    toggleBtn.setAttribute('aria-controls', panelId);
                    updateToggleIconButton(toggleBtn, isExpanded, referenceLabel);

                    refRow.appendChild(refSpan);
                    refRow.appendChild(toggleBtn);

                    const verseArea = document.createElement('div');
                    verseArea.className = 'attached-about-area scripture-verse-area';
                    verseArea.id = panelId;
                    verseArea.style.display = isExpanded ? 'block' : 'none';

                    const versePara = document.createElement('p');
                    versePara.className = 'about-text scripture-verse-text';
                    versePara.textContent = verseText;

                    verseArea.appendChild(versePara);
                    itemDiv.appendChild(refRow);
                    itemDiv.appendChild(verseArea);
                    listDiv.appendChild(itemDiv);
                });

                introCard.appendChild(listDiv);
            }

            introRow.appendChild(introCard);
            targetContainer.appendChild(introRow);
        }

        // Helper function to render the closing Scripture card ("What Is and What Is to Come...")
        function renderClosingScriptureSection(targetContainer, currentLangData) {
            const enTrans = (bstData.translations && bstData.translations.en) || {};

            // 1. Render closing card headed by string_id: timeline_closing_title
            const closingRow = document.createElement('article');
            closingRow.className = 'layout-row timeline-closing-row';

            const closingCard = document.createElement('div');
            closingCard.className = 'node-card role-primary_sequence timeline-closing-card';

            const headerDiv = document.createElement('div');
            headerDiv.className = 'node-header';

            const headingSpan = document.createElement('span');
            headingSpan.className = 'node-title timeline-closing-title';
            const rawHeading = currentLangData.strings && currentLangData.strings.timeline_closing_title;
            headingSpan.textContent = (rawHeading !== undefined && rawHeading !== null && String(rawHeading).trim() !== '')
                ? String(rawHeading)
                : ((enTrans.strings && enTrans.strings.timeline_closing_title) || 'What Is and What Is to Come...');
            headerDiv.appendChild(headingSpan);
            closingCard.appendChild(headerDiv);

            // 2. Render Scripture references and independent Show/Hide verse panels
            const passagesMetaMap = (bstData.metadata && bstData.metadata.scripture_passages) || {};
            const CLOSING_PASSAGE_IDS = ['revelation_1_19', 'revelation_21_1'];
            const orderedPassages = CLOSING_PASSAGE_IDS
                .map(id => passagesMetaMap[id])
                .filter(Boolean)
                .sort((a, b) => {
                    const seqA = (a.sequence_order !== undefined && a.sequence_order !== null) ? a.sequence_order : 0;
                    const seqB = (b.sequence_order !== undefined && b.sequence_order !== null) ? b.sequence_order : 0;
                    return seqA - seqB;
                });

            if (orderedPassages.length > 0) {
                const listDiv = document.createElement('div');
                listDiv.className = 'creation-scripture-list closing-scripture-list';

                const langScriptureMap = currentLangData.scripture || {};

                orderedPassages.forEach(pMeta => {
                    const passageId = pMeta.passage_id;
                    const bookId = pMeta.book_id;
                    const bookTrans = (currentLangData.books && currentLangData.books[bookId]) ||
                        (enTrans.books && enTrans.books[bookId]) || {};
                    const bookFullName = bookTrans.name_full || bookId;
                    const verseRange = (pMeta.verse_end && pMeta.verse_end !== pMeta.verse_start)
                        ? `${pMeta.verse_start}-${pMeta.verse_end}`
                        : `${pMeta.verse_start}`;
                    const referenceLabel = `${bookFullName} ${pMeta.chapter}:${verseRange}`;

                    const langPassage = langScriptureMap[passageId] || {};
                    const verseText = typeof langPassage.verse_text === 'string' ? langPassage.verse_text : '';

                    const isExpanded = expandedScripturePassages.has(passageId);
                    const panelId = `scripture-verse-${sanitizeId(passageId)}`;

                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'creation-scripture-item closing-scripture-item';
                    itemDiv.setAttribute('data-passage-id', passageId);

                    const refRow = document.createElement('div');
                    refRow.className = 'creation-scripture-row closing-scripture-row';

                    const refSpan = document.createElement('span');
                    refSpan.className = 'creation-scripture-ref closing-scripture-ref';
                    refSpan.textContent = referenceLabel;

                    const toggleBtn = document.createElement('button');
                    toggleBtn.type = 'button';
                    toggleBtn.className = 'toggle-scripture-btn';
                    toggleBtn.setAttribute('data-passage-id', passageId);
                    toggleBtn.setAttribute('data-reference-label', referenceLabel);
                    toggleBtn.setAttribute('aria-controls', panelId);
                    updateToggleIconButton(toggleBtn, isExpanded, referenceLabel);

                    refRow.appendChild(refSpan);
                    refRow.appendChild(toggleBtn);

                    const verseArea = document.createElement('div');
                    verseArea.className = 'attached-about-area scripture-verse-area';
                    verseArea.id = panelId;
                    verseArea.style.display = isExpanded ? 'block' : 'none';

                    const versePara = document.createElement('p');
                    versePara.className = 'about-text scripture-verse-text';
                    versePara.textContent = verseText;

                    verseArea.appendChild(versePara);
                    itemDiv.appendChild(refRow);
                    itemDiv.appendChild(verseArea);
                    listDiv.appendChild(itemDiv);
                });

                closingCard.appendChild(listDiv);
            }

            closingRow.appendChild(closingCard);
            targetContainer.appendChild(closingRow);
        }

        // Helper function to render transition period cards
        function renderTransitionPeriod(node) {
            const row = document.createElement('article');
            row.className = 'layout-row intertestamental-row historical-row';
            const label = getNodeLabel(node);
            let dates = '';
            if (node.start_value !== null && node.end_value !== null) {
                const formatBC = (val) => Math.abs(val) + (val < 0 ? ' BC' : ' AD');
                dates = `${formatBC(node.start_value)} - ${formatBC(node.end_value)}`;
            }
            row.innerHTML = `
                <div class="intertestamental-container transition-period">
                    <h3>${label}</h3>
                    <p>${dates}</p>
                </div>
            `;
            return row;
        }

        // Helper function to render a spine row for secondary books only (marker-anchored)
        function renderSpineRowForSecondaryOnly(attachedBooks) {
            const row = document.createElement('article');
            row.className = 'layout-row secondary-only-row historical-row';

            // Compile Attached Books Grid HTML
            const booksHtml = `
                <div class="attached-books-header">
                    <h4>${escapeAttr(t('related_books'))}</h4>
                </div>
                <div class="attached-books-grid">
                    ${attachedBooks.map(s => {
                        const sColor = getNodeColor(s) || 'var(--color-book)';
                        const sRefId = s.reference_id;
                        const sBookTrans = (sRefId && langData.books && langData.books[sRefId]) || {};
                        
                        // Title fallback
                        const sTitleRef = sRefId || s.node_id;
                        let sAbbr = sBookTrans.name_abbr || sBookTrans.name_full || sTitleRef;
                        sAbbr = String(sAbbr).toUpperCase();
                        
                        // Subtitle fallback
                        const sSubtitleRef = sRefId || s.node_id;
                        let sFull = sBookTrans.name_full || sSubtitleRef;

                        const sNumRaw = getBookNumberRaw(sRefId);
                        let sBadgeHtml = '';
                        let sHasNumberClass = '';
                        if (sNumRaw) {
                            sBadgeHtml = `<div class="book-number-badge">${sNumRaw}</div>`;
                            sHasNumberClass = 'has-book-number';
                        }

                        const sClassificationLine = getBookClassificationLine(s);
                        const sMetadataHtml = sClassificationLine
                            ? `<div class="book-classification">${sClassificationLine}</div>`
                            : '';

                        const sAboutText = getAboutText(s);
                        let sAboutBtnHtml = '';
                        let sAboutAreaHtml = '';
                        if (sAboutText) {
                            const isAboutExpanded = expandedAboutNodes.has(s.node_id);
                            const aboutDisplayStyle = isAboutExpanded ? 'display: block;' : 'display: none;';
                            const sAboutPanelId = `about-area-${sanitizeId(s.node_id)}`;
                            
                            sAboutBtnHtml = renderAboutToggleButtonHtml(s.node_id, sFull, isAboutExpanded, sAboutPanelId);
                            
                            sAboutAreaHtml = `
                                <div class="attached-about-area" id="${sAboutPanelId}" style="${aboutDisplayStyle}">
                                    <p class="about-text">${sAboutText}</p>
                                </div>
                            `;
                        }

                        return `
                            <div class="attached-book-card ${sHasNumberClass}" style="--node-color-code: ${sColor}">
                                <div class="attached-book-header">
                                    <div class="book-title-group">
                                        <div class="book-title-row">
                                            <span class="attached-book-abbr">${sAbbr}</span>
                                        </div>
                                        <span class="attached-book-subtitle">${sFull}</span>
                                    </div>
                                    ${sBadgeHtml}
                                </div>
                                ${sMetadataHtml}
                                ${sAboutAreaHtml}
                                ${sAboutBtnHtml ? `<div class="card-actions">${sAboutBtnHtml}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            `;

            row.innerHTML = `
                <div class="secondary-track">
                    ${booksHtml}
                </div>
            `;

            (historicalWrapper || spineContainer).appendChild(row);
        }

        // Helper function to render a single spine row
        function renderSpineRow(primaryNode, attachedBooks, attachedChips, hasAnchor, anchorId) {
            const row = document.createElement('article');
            row.className = 'layout-row historical-row';

            // Resolve Primary Node title details
            let titleHtml = '';
            const pColor = getNodeColor(primaryNode) || 'var(--color-book)';
            let badgeHtml = '';
            let hasNumberClass = '';
            let primaryBookFullName = '';
            
            if (primaryNode.node_type === 'book') {
                const pRefId = primaryNode.reference_id;
                const pBookTrans = (pRefId && langData.books && langData.books[pRefId]) || {};
                
                // Title fallback
                const titleRef = pRefId || primaryNode.node_id;
                let pAbbr = pBookTrans.name_abbr || pBookTrans.name_full || titleRef;
                pAbbr = String(pAbbr).toUpperCase();
                
                // Subtitle fallback
                const subtitleRef = pRefId || primaryNode.node_id;
                let pFull = pBookTrans.name_full || subtitleRef;
                primaryBookFullName = pFull;

                const rawNum = getBookNumberRaw(pRefId);
                if (rawNum) {
                    badgeHtml = `<div class="book-number-badge">${rawNum}</div>`;
                    hasNumberClass = 'has-book-number';
                }

                titleHtml = `
                    <div class="book-title-group">
                        <div class="book-title-row">
                            <span class="node-title book-abbr">${pAbbr}</span>
                        </div>
                        <span class="book-subtitle">${pFull}</span>
                    </div>
                `;
            } else {
                const label = getNodeLabel(primaryNode);
                primaryBookFullName = label;
                titleHtml = `<span class="node-title">${label}</span>`;
            }

            // Compile Chips HTML (People & Events) and Toggle Control
            let chipsHtml = '';
            let toggleBtnHtml = '';
            const validChips = attachedChips.filter(ch => {
                const label = getNodeLabel(ch);
                return label !== null && label !== undefined && String(label).trim() !== '';
            });
            if (validChips.length > 0) {
                const isExpanded = expandedNodes.has(primaryNode.node_id);
                const displayStyle = isExpanded ? 'display: block;' : 'display: none;';
                const chipsPanelId = `chips-area-${sanitizeId(primaryNode.node_id)}`;

                toggleBtnHtml = renderChipsToggleButtonHtml(primaryNode.node_id, primaryBookFullName, isExpanded, chipsPanelId);

                chipsHtml = `
                    <div class="attached-chips-area" id="${chipsPanelId}" style="${displayStyle}">
                        <h4>${escapeAttr(t('people_and_events'))}</h4>
                        <div class="chips-container">
                            ${validChips.map(ch => {
                                const chLabel = getNodeLabel(ch);
                                const chColor = getNodeColor(ch) || '#9ca3af';
                                return `
                                    <span class="chip" style="--chip-color: ${chColor}">
                                        <span class="chip-dot"></span>
                                        <span class="chip-text">${chLabel}</span>
                                    </span>
                                `;
                            }).join('')}
                        </div>
                    </div>
                `;
            }

            // Compile Attached Books Grid HTML
            let booksHtml = '';
            if (attachedBooks.length > 0) {
                booksHtml = `
                    <div class="attached-books-header">
                        <h4>${escapeAttr(t('related_books'))}</h4>
                    </div>
                    <div class="attached-books-grid">
                        ${attachedBooks.map(s => {
                            const sColor = getNodeColor(s) || 'var(--color-book)';
                            const sRefId = s.reference_id;
                            const sBookTrans = (sRefId && langData.books && langData.books[sRefId]) || {};
                            
                            // Title fallback
                            const sTitleRef = sRefId || s.node_id;
                            let sAbbr = sBookTrans.name_abbr || sBookTrans.name_full || sTitleRef;
                            sAbbr = String(sAbbr).toUpperCase();
                            
                            // Subtitle fallback
                            const sSubtitleRef = sRefId || s.node_id;
                            let sFull = sBookTrans.name_full || sSubtitleRef;

                            const sNumRaw = getBookNumberRaw(sRefId);
                            let sBadgeHtml = '';
                            let sHasNumberClass = '';
                            if (sNumRaw) {
                                sBadgeHtml = `<div class="book-number-badge">${sNumRaw}</div>`;
                                sHasNumberClass = 'has-book-number';
                            }

                            const sClassificationLine = getBookClassificationLine(s);
                            const sMetadataHtml = sClassificationLine
                                ? `<div class="book-classification">${sClassificationLine}</div>`
                                : '';

                            const sAboutText = getAboutText(s);
                            let sAboutBtnHtml = '';
                            let sAboutAreaHtml = '';
                            if (sAboutText) {
                                const isAboutExpanded = expandedAboutNodes.has(s.node_id);
                                const aboutDisplayStyle = isAboutExpanded ? 'display: block;' : 'display: none;';
                                const sAboutPanelId = `about-area-${sanitizeId(s.node_id)}`;
                                
                                sAboutBtnHtml = renderAboutToggleButtonHtml(s.node_id, sFull, isAboutExpanded, sAboutPanelId);
                                
                                sAboutAreaHtml = `
                                    <div class="attached-about-area" id="${sAboutPanelId}" style="${aboutDisplayStyle}">
                                        <p class="about-text">${sAboutText}</p>
                                    </div>
                                `;
                            }

                            return `
                                <div class="attached-book-card ${sHasNumberClass}" style="--node-color-code: ${sColor}">
                                    <div class="attached-book-header">
                                        <div class="book-title-group">
                                            <div class="book-title-row">
                                                <span class="attached-book-abbr">${sAbbr}</span>
                                            </div>
                                            <span class="attached-book-subtitle">${sFull}</span>
                                        </div>
                                        ${sBadgeHtml}
                                    </div>
                                    ${sMetadataHtml}
                                    ${sAboutAreaHtml}
                                    ${sAboutBtnHtml ? `<div class="card-actions">${sAboutBtnHtml}</div>` : ''}
                                </div>
                            `;
                        }).join('')}
                    </div>
                `;
            } else {
                booksHtml = '';
            }

            // Construct Row Inner HTML
            const isTransition = primaryNode.timeline_role === 'transition_period';
            const transitionClass = isTransition ? 'transition-period' : '';

            let pMetadataHtml = '';
            let aboutBtnHtml = '';
            let aboutAreaHtml = '';
            let datesHtml = '';

            // Babylonian Exile (node_babylonian_exile) uses its localized timeline marker
            // (date_608_538_bc) as the single reader-facing date range, avoiding duplicate
            // and inconsistent date displays.
            if (
                (primaryNode.timeline_role === 'transition_period' || primaryNode.node_type === 'timeline_break') &&
                primaryNode.node_id !== 'node_babylonian_exile'
            ) {
                if (primaryNode.start_value !== null && primaryNode.end_value !== null) {
                    const formatBC = (val) => Math.abs(val) + (val < 0 ? ' BC' : ' AD');
                    const datesStr = `${formatBC(primaryNode.start_value)} - ${formatBC(primaryNode.end_value)}`;
                    datesHtml = `<p class="transition-dates">${datesStr}</p>`;
                }
            }

            if (primaryNode.node_type === 'book') {
                const pClassificationLine = getBookClassificationLine(primaryNode);
                pMetadataHtml = pClassificationLine
                    ? `<div class="book-classification">${pClassificationLine}</div>`
                    : '';

                const aboutText = getAboutText(primaryNode);
                if (aboutText) {
                    const isAboutExpanded = expandedAboutNodes.has(primaryNode.node_id);
                    const aboutDisplayStyle = isAboutExpanded ? 'display: block;' : 'display: none;';
                    const pAboutPanelId = `about-area-${sanitizeId(primaryNode.node_id)}`;

                    aboutBtnHtml = renderAboutToggleButtonHtml(primaryNode.node_id, primaryBookFullName, isAboutExpanded, pAboutPanelId);

                    aboutAreaHtml = `
                        <div class="attached-about-area" id="${pAboutPanelId}" style="${aboutDisplayStyle}">
                            <p class="about-text">${aboutText}</p>
                        </div>
                    `;
                }
            } else {
                pMetadataHtml = '';
            }

            // Check if this is the Matthew Gospel row and render the localized emphasis statement
            let gospelEmphasisHtml = '';
            if (primaryNode.node_id === 'node_matthew' || primaryNode.reference_id === 'matthew') {
                const enTrans = (bstData.translations && bstData.translations.en) || {};
                const rawGospelStatement = langData.strings && langData.strings.resurrection_of_jesus_best_attested;
                const statementText = (rawGospelStatement !== undefined && rawGospelStatement !== null && String(rawGospelStatement).trim() !== '')
                    ? String(rawGospelStatement)
                    : ((enTrans.strings && enTrans.strings.resurrection_of_jesus_best_attested) || 'The Resurrection of Jesus is the Best Attested Event in History');

                gospelEmphasisHtml = `
                <!-- Gospel Emphasis Statement -->
                <div class="gospel-emphasis-container" data-statement-id="resurrection_of_jesus_best_attested">
                    <div class="gospel-emphasis-divider" aria-hidden="true"></div>
                    <p class="gospel-emphasis-statement">${statementText}</p>
                </div>
                `;
            }

            row.innerHTML = `
                <!-- Primary Node Card & Its Attached Chips -->
                <div class="primary-track">
                    <div class="node-card type-${primaryNode.node_type || 'default'} role-${primaryNode.timeline_role || 'none'} ${transitionClass} ${hasNumberClass}" style="--node-color-code: ${pColor}">
                        <div class="node-header">
                            ${titleHtml}
                            ${badgeHtml}
                        </div>
                        
                        ${datesHtml}

                        ${pMetadataHtml}
                        
                        ${aboutAreaHtml}
                        
                        ${(aboutBtnHtml || toggleBtnHtml) ? `
                            <div class="card-actions">
                                ${aboutBtnHtml}
                                ${toggleBtnHtml}
                            </div>
                        ` : ''}
                    </div>
                    
                    ${chipsHtml}
                </div>
                ${booksHtml ? `
                <!-- Related Books Grid -->
                <div class="secondary-track">
                    ${booksHtml}
                </div>
                ` : ''}
                ${gospelEmphasisHtml}
            `;

            (historicalWrapper || spineContainer).appendChild(row);
        }
    }

    /**
     * Resolves value safety for sorting
     */
    function getSortVal(val) {
        return (val !== undefined && val !== null) ? val : null;
    }

    /**
     * Comparator matching user sorting rules
     */
    function compareNodes(a, b) {
        // 1. sequence_order if present
        const seqA = getSortVal(a.sequence_order);
        const seqB = getSortVal(b.sequence_order);
        if (seqA !== null && seqB !== null) {
            if (seqA !== seqB) return seqA - seqB;
        } else if (seqA !== null) {
            return -1; // values present sort before missing ones
        } else if (seqB !== null) {
            return 1;
        }

        // 2. cluster_sort_order if present
        const clusA = getSortVal(a.cluster_sort_order);
        const clusB = getSortVal(b.cluster_sort_order);
        if (clusA !== null && clusB !== null) {
            if (clusA !== clusB) return clusA - clusB;
        } else if (clusA !== null) {
            return -1;
        } else if (clusB !== null) {
            return 1;
        }

        // 3. axis_value if present
        const axisA = getSortVal(a.axis_value);
        const axisB = getSortVal(b.axis_value);
        if (axisA !== null && axisB !== null) {
            if (axisA !== axisB) return axisA - axisB;
        } else if (axisA !== null) {
            return -1;
        } else if (axisB !== null) {
            return 1;
        }

        // 4. node_id
        const idA = a.node_id || '';
        const idB = b.node_id || '';
        return idA.localeCompare(idB);
    }

    /**
     * Reads the testament book number and returns it formatted with leading zero.
     * e.g., 1 -> "01"
     */
    function getBookNumberRaw(referenceId) {
        if (!referenceId || !bstData || !bstData.metadata || !bstData.metadata.books) {
            return '';
        }
        const bookMeta = bstData.metadata.books[referenceId];
        if (!bookMeta) return '';
        const num = bookMeta.testament_book_number;
        if (num === undefined || num === null) return '';
        return String(num).padStart(2, '0');
    }

    /**
     * Helper to translate metadata labels (role groups/subsections)
     */
    function translateMetadataLabel(role, value) {
        if (value === undefined || value === null) {
            return '';
        }
        const strVal = String(value).trim();
        if (strVal === '') {
            return '';
        }

        const stringId = `${role}_${strVal}`;

        // 1. Try currently selected language
        const langData = bstData.translations[currentLang] || {};
        const strings = langData.strings || {};
        if (strings[stringId] !== undefined && strings[stringId] !== null && String(strings[stringId]).trim() !== '') {
            return String(strings[stringId]);
        }

        // 2. Try English fallback
        const enData = bstData.translations['en'] || {};
        const enStrings = enData.strings || {};
        if (enStrings[stringId] !== undefined && enStrings[stringId] !== null && String(enStrings[stringId]).trim() !== '') {
            return String(enStrings[stringId]);
        }

        // 3. Raw fallback (underscores replaced by spaces)
        return strVal.replace(/_/g, ' ');
    }

    /**
     * Resolves and formats the reader-facing book classification line.
     */
    function getBookClassificationLine(node) {
        if (!node || node.node_type !== 'book' || !node.reference_id) {
            return '';
        }
        if (!bstData || !bstData.metadata || !bstData.metadata.books) {
            return '';
        }
        const bookMeta = bstData.metadata.books[node.reference_id];
        if (!bookMeta) {
            return '';
        }

        const testament = bookMeta.testament;
        const parts = [];

        if (testament === 'old_testament') {
            const group = translateMetadataLabel('traditional_group', bookMeta.traditional_group);
            const struct = translateMetadataLabel('hebrew_structure', bookMeta.hebrew_structure);
            const sub = translateMetadataLabel('hebrew_subsection', bookMeta.hebrew_subsection);
            
            if (group) parts.push(group);
            if (struct) parts.push(struct);
            if (sub) parts.push(sub);
        } else if (testament === 'new_testament') {
            const group = translateMetadataLabel('traditional_group', bookMeta.traditional_group);
            const greekSub = translateMetadataLabel('greek_subsection', bookMeta.greek_subsection);
            if (group) parts.push(group);
            if (greekSub) parts.push(greekSub);
        } else {
            // Fallback
            const group = translateMetadataLabel('traditional_group', bookMeta.traditional_group);
            if (group) parts.push(group);
        }

        return parts.join(' | ');
    }

    /**
     * Resolves the translated About description string for a book node.
     * Uses currently selected language when available, falling back to English.
     */
    function getAboutText(node) {
        if (!node || node.node_type !== 'book' || !node.reference_id) {
            return '';
        }
        if (!bstData || !bstData.translations) {
            return '';
        }
        const stringId = `about_${node.reference_id}`;

        // 1. Try currently selected language
        const langData = bstData.translations[currentLang] || {};
        const strings = langData.strings || {};
        if (strings[stringId] !== undefined && strings[stringId] !== null && String(strings[stringId]).trim() !== '') {
            return String(strings[stringId]);
        }

        // 2. Try English fallback
        const enData = bstData.translations['en'] || {};
        const enStrings = enData.strings || {};
        if (enStrings[stringId] !== undefined && enStrings[stringId] !== null && String(enStrings[stringId]).trim() !== '') {
            return String(enStrings[stringId]);
        }

        return '';
    }

    /**
     * Translates a node's display name based on user specifications:
     * - For book nodes, display the translated book name from translations[lang].books[reference_id].
     * - For string/event/person/category/marker nodes, display the translated string from translations[lang].strings[reference_id].
     * - If a label is missing, fall back to reference_id.
     * - If reference_id is null, fall back to node_id.
     *  */
    function getNodeLabel(node) {
        const langData = bstData.translations[currentLang] || {};
        const refId = node.reference_id;
        const fallback = refId || node.node_id;

        if (node.node_type === 'book') {
            const bookTrans = langData.books || {};
            if (refId && bookTrans[refId]) {
                const transObj = bookTrans[refId];
                // Try full name first, fall back to abbr, then fallback
                return transObj.name_full || transObj.name_abbr || fallback;
            }
        } else {
            const stringTrans = langData.strings || {};
            if (refId && stringTrans[refId] !== undefined && stringTrans[refId] !== null) {
                return String(stringTrans[refId]);
            }
        }

        return fallback;
    }

    /**
     * Determines color coding based on node type and book categories:
     * - history books: #990000
     * - poetry books and letters books: #446644
     * - prophecy books: #003399
     * - people/person nodes: #ff5500
     * - event nodes: #999999
     * - timeline markers/time markers: #cc9933
     */
    function getNodeColor(node) {
        if (node.timeline_role === 'transition_period') {
            return '#555555';
        }
        if (node.node_type === 'book') {
            const refId = node.reference_id;
            const bookMeta = (refId && bstData.metadata && bstData.metadata.books && bstData.metadata.books[refId]) || {};
            const category = bookMeta.category_bst;
            
            if (category === 'history') {
                return '#990000';
            } else if (category === 'poetry' || category === 'letters') {
                return '#446644';
            } else if (category === 'prophecy') {
                return '#003399';
            }
            return '#1f2937'; // Default book color
        }
        
        if (node.node_type === 'category_label') {
            const refId = node.reference_id;
            if (refId === 'history') {
                return '#990000';
            } else if (refId === 'poetry' || refId === 'letters') {
                return '#446644';
            } else if (refId === 'prophecy') {
                return '#003399';
            }
        }
        
        if (node.node_type === 'person') {
            return '#ff5500';
        }
        
        if (node.node_type === 'event') {
            return '#999999';
        }
        
        if (node.node_type === 'timeline_marker' || node.node_type === 'time_marker') {
            return '#cc9933';
        }
        
        return null;
    }





    /**
     * Retrieves all timeline markers for a given timeline ID, sorted.
     */
    function getTimelineMarkers(timelineId) {
        if (!bstData || !bstData.metadata || !bstData.metadata.timeline_nodes) return [];
        const markers = Object.entries(bstData.metadata.timeline_nodes)
            .map(([nodeId, nodeObj]) => ({ node_id: nodeId, ...nodeObj }))
            .filter(node => 
                (node.node_type === 'timeline_marker' || node.node_type === 'time_marker') &&
                node.timeline_id === timelineId
            );
        return sortMarkers(markers);
    }

    /**
     * Comparator specifically for timeline markers.
     */
    function sortMarkers(markers) {
        return [...markers].sort((a, b) => {
            if (a.sequence_order !== undefined && a.sequence_order !== null &&
                b.sequence_order !== undefined && b.sequence_order !== null) {
                if (a.sequence_order !== b.sequence_order) {
                    return a.sequence_order - b.sequence_order;
                }
            }
            if (a.axis_value !== undefined && a.axis_value !== null &&
                b.axis_value !== undefined && b.axis_value !== null) {
                if (a.axis_value !== b.axis_value) {
                    return a.axis_value - b.axis_value;
                }
            }
            return a.node_id.localeCompare(b.node_id);
        });
    }

    // New Testament decade marker string IDs.
    // Note: Internal identifiers (e.g., date_40s, node_decade_40s) do not dictate displayed text.
    // The numeric-only display values ("30".."90") in the source data are an intentional
    // presentation choice and must remain numeric-only (without "s" or "AD") unless an
    // explicit editorial decision changes the convention.
    const NT_DECADE_MARKER_IDS = new Set([
        'date_30s',
        'date_40s',
        'date_50s',
        'date_60s',
        'date_70s',
        'date_80s',
        'date_90s'
    ]);

    // Set of non-numeric timeline marker string IDs that must remain untouched
    const NON_DATE_MARKER_IDS = new Set([
        'event_creation',
        'date_present',
        'date_future'
    ]);

    /**
     * Centralizes timeline date marker formatting while preserving localized strings
     * and underlying numerical values.
     * - Preserves non-date labels (e.g., Present, Future, and localized equivalents).
     * - Preserves NT decade markers (date_30s..date_90s) as bare numbers ("30".."90")
     *   across all languages; internal IDs like date_40s do not dictate displayed text.
     * - Normalizes BC date markers: replaces spaced hyphen ranges between numbers/? with
     *   an en dash (e.g., "608 - 538" -> "608–538") and normalizes attached "bc" suffixes
     *   to spaced uppercase " BC" (e.g., "4000bc" -> "4000 BC", "608 - 538bc" -> "608–538 BC"),
     *   while preserving localized era labels (e.g., "ق.م", "av. J.-C.", "Kr. e.", "紀元前").
     */
    function formatTimelineMarkerLabel(rawLabel, refId) {
        if (rawLabel === null || rawLabel === undefined) {
            return '';
        }
        const rawStr = String(rawLabel).trim();
        if (rawStr === '') {
            return '';
        }

        // 1. Preserve non-date labels (Present, Future) and numeric-only NT decade labels (30..90).
        // NT markers intentionally display bare numbers without "s" or "AD" suffixes.
        if (NON_DATE_MARKER_IDS.has(refId) || NT_DECADE_MARKER_IDS.has(refId)) {
            return rawStr;
        }

        // 2. Normalize BC and range date markers
        // Convert spaced hyphen ranges between numbers or '?' to an en dash (–)
        let formatted = rawStr.replace(/(\d|\?)\s*-\s*(\d)/g, '$1\u2013$2');
        // Convert attached or lowercase 'bc' suffix after a number to spaced uppercase ' BC'
        formatted = formatted.replace(/(\d)\s*bc\b/gi, '$1 BC');

        return formatted;
    }

    /**
     * Resolves the user-facing label for a marker node, utilizing translations
     * and centralized date marker formatting.
     */
    function getMarkerLabel(markerNode) {
        const langData = bstData.translations[currentLang] || {};
        const refId = markerNode.reference_id || markerNode.node_id;
        if (langData.strings && langData.strings[refId] !== undefined && langData.strings[refId] !== null) {
            return formatTimelineMarkerLabel(langData.strings[refId], refId);
        }
        if (refId === 'date_608_538_bc') {
            return '608\u2013538 BC';
        }
        const fallbackLabel = markerNode.node_id.replace('node_', '').replace('decade_', '').replace('_', ' ').toUpperCase();
        return formatTimelineMarkerLabel(fallbackLabel, refId);
    }

    /**
     * Renders a timeline marker as a full-width divider break.
     */
    function renderTimelineMarker(markerNode) {
        if (!markerNode) return null;
        // Suppress Present and Future date markers from the rendered timeline
        const refId = markerNode.reference_id || markerNode.node_id;
        if (markerNode.node_id === 'marker_present' || markerNode.node_id === 'marker_future' ||
            refId === 'date_present' || refId === 'date_future') {
            return null;
        }
        const label = getMarkerLabel(markerNode);
        if (label === null || label === undefined || String(label).trim() === '') {
            return null;
        }
        const divider = document.createElement('div');
        divider.className = 'timeline-time-divider';
        const markerColor = getNodeColor(markerNode) || '#cc9933';
        divider.style.setProperty('--node-color-code', markerColor);
        if (markerNode.node_id === 'node_creation_intro_marker' || markerNode.reference_id === 'event_creation') {
            divider.classList.add('creation-time-divider');
            divider.setAttribute('data-marker-role', 'creation');
            divider.innerHTML = `<span class="timeline-marker-pill">${label}</span>`;
        } else {
            divider.classList.add('historical-marker');
            divider.setAttribute('data-marker-id', markerNode.node_id);
            divider.innerHTML = `<div class="timeline-marker-track"><span class="timeline-marker-dot" aria-hidden="true"></span><span class="timeline-marker-connector" aria-hidden="true"></span><span class="timeline-marker-pill">${label}</span></div>`;
        }
        return divider;
    }

    /**
     * Resolves the primary marker ID that a node ultimately anchors to by tracing anchor chains.
     */
    function getMarkerAnchorId(nodeId, allNodes) {
        let curr = nodeId;
        const visited = new Set();
        while (curr && !visited.has(curr)) {
            visited.add(curr);
            const node = allNodes.find(n => n.node_id === curr);
            if (!node) break;
            if ((node.node_type === 'timeline_marker' || node.node_type === 'time_marker') &&
                (node.timeline_id === 'ot_date_markers' || node.timeline_id === 'nt_date_markers' || node.timeline_id === 'main_spine')) {
                return curr;
            }
            curr = node.anchor_node_id;
        }
        return null;
    }
});

