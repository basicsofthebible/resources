# Bible Structure and Timeline (BST) Interactive

An interactive, multilingual web application that visualizes the structure of the Bible arranged in chronological sequence.

**BST Interactive** is an interactive digital adaptation of the original [Bible Structure and Timeline (BST)](https://basicsofthebible.org/bst/) graphic published by [basicsofthebible.org](https://basicsofthebible.org).

- **Live Application:** [https://basicsofthebible.github.io/resources/bstinteractive/](https://basicsofthebible.github.io/resources/bstinteractive/)
- **Original Graphic:** [https://basicsofthebible.org/bst/](https://basicsofthebible.org/bst/)

---

## Features

- **Chronological Biblical Presentation:** Explores the 66 books of the Bible along an interactive historical timeline, highlighting primary narrative books and concurrent historical periods.
- **Rich Contextual Information:** Expandable details for each book, including summaries, key biblical events, notable people, and foundational Scripture passages.
- **Multilingual Support:** Fully localized across 22 languages with native bidirectional text rendering (LTR and RTL):
  - English
  - Arabic (العربية)
  - Bengali (বাংলা)
  - Chinese Simplified (简体中文)
  - Chinese Traditional (繁體中文)
  - Chinese Pinyin (Pīnyīn)
  - French (Français)
  - German (Deutsch)
  - Hindi (हिन्दी)
  - Hungarian (Magyar)
  - Indonesian (Bahasa Indonesia)
  - Italian (Italiano)
  - Japanese (日本語)
  - Khmer (ខ្មែរ)
  - Korean (한국어)
  - Persian (فارسی)
  - Portuguese (Português)
  - Russian (Русский)
  - Spanish (Español)
  - Tamil (தமிழ்)
  - Thai (ไทย)
  - Vietnamese (Tiếng Việt)
- **Theme Support:** Clean light and dark modes with automated system preference detection and accessible color contrast.
- **Responsive Design:** Optimized for devices of all sizes, from wide desktop screens to mobile smartphones, featuring floating and docked color key legends.

---

## Public Master Data Workbook

The structured dataset powering BST Interactive is maintained in a master spreadsheet and compiled into the web application's JSON data resources.

- **Download:** [Download the master data workbook](./downloads/bst_master_data.xlsx)

The workbook includes complete book metadata, historical dates, sequence classifications, event/people associations, localized book titles, and multilingual Scripture quotations with edition provenance and permission statuses.

---

## Running Locally

BST Interactive is built with vanilla HTML, CSS, and modern JavaScript, requiring no build tools, bundlers, or package managers.

To run the site locally:

1. Clone or download this repository.
2. Open a terminal in this directory (`docs/bstinteractive/`).
3. Start any standard local HTTP server:

   **Using Python:**
   ```bash
   python -m http.server 8000
   ```

   **Using Node.js (`npx serve`):**
   ```bash
   npx serve .
   ```

4. Open `http://localhost:8000/` in your web browser.

*(Note: Because the application loads its data resources via standard `fetch()` requests, it must be served through an HTTP server rather than opened directly as a `file://` URL.)*

---

## Credits, Licenses & Permissions

- **Original Concept, Design & Compilation:** Shawn Handran &bull; [basicsofthebible.org](https://basicsofthebible.org)
- **Scripture Texts & Translations:** Scripture quotations in this work are drawn from multiple Bible translations and editions across 22 languages. Each translation is used in accordance with its respective publisher permissions, public domain status, or Creative Commons license.
- **Detailed Sources:** For specific Scripture translation versions, copyrights, licensing details, and permission notices for each language, please refer to the **References & Permissions** section within the interactive application.
