# FOSPX PDF Editor
<img src="https://raw.githubusercontent.com/fospx-org/fospx-pdf-editor/refs/heads/main/fospx_pdf_editor/img/f-pv1.svg" width="256" height="256"/>

**FOSPX PDF Editor** is a simple and user-friendly tool developed for Pardus, Debian and other Linux distributions, focused on editing text content in PDF files. Developed from scratch in the spirit of #MilliTeknolojiHamlesi and TEKNOFEST to meet a need in the Pardus ecosystem, FOSPX PDF Editor is a free and open PDF editor serving both corporate and individual users — and the **FIRST PLACE** winner of the TEKNOFEST Pardus Development Competition.

Developer: **Barın Güzeldemirci (word-sys) [FOSPX]**  
License: **GPL-3.0-or-later**

---

> [!TIP]
> **Recommended Stable Release: v1.8.3** — For the most stable experience, it is strongly recommended to use version **1.8.3**. See the installation sections below for details on how to install this version.

---

<img src="https://raw.githubusercontent.com/fospx-org/fospx-pdf-editor/refs/heads/main/screenshots/screenshot1.png" width="1460" height="960"/>
<img src="https://raw.githubusercontent.com/fospx-org/fospx-pdf-editor/refs/heads/main/screenshots/screenshot2.png" width="1460" height="960"/>

## Key Features

*   Create PDF files
*   Open and view PDF files
*   Select existing text blocks within a page
*   Edit or delete selected text
*   Add new text blocks to a page
*   Add images to a page
*   Font width/family support
*   Move/reposition objects within the PDF
*   Change font type, size, and color
*   Save edited PDFs
*   Export PDFs to DOCX or ODT (requires LibreOffice) and TXT formats
*   User-friendly interface with page previews
*   Safe Save
*   Restricted Mode (Safe Mode)
*   Undo/Redo changes
*   Merge PDFs
*   Add/remove pages from PDFs
*   Add shapes to PDFs

---

## Installation

There are two ways to install FOSPX PDF Editor on your system:

### 1. Automatic Installation (Recommended Method)

This method is the easiest installation path for Linux distributions.

1.  Download the latest `.deb` package from the [**GitHub Releases**](https://github.com/fospx-org/fospx-pdf-editor/releases) page. The file will typically be named something like `fospx-pdf-editor_1.8.3_all.deb`.

    > [!TIP]
    > **Use version 1.8.3** for the most stable experience: look for `fospx-pdf-editor_1.8.3_all.deb` on the releases page.

2.  Open a terminal in the directory where you downloaded the `.deb` file.
3.  Run the following command to install the package:
    ```bash
    sudo apt update
    sudo apt install ./fospx-pdf-editor_1.8.3_all.deb
    ```
    *(Note: Replace `fospx-pdf-editor_1.8.3_all.deb` with the exact filename you downloaded if different.)*
4.  If you encounter a dependency error during installation, try running the following command to fix missing dependencies:
    ```bash
    sudo apt --fix-broken install
    ```
5.  Once installation is complete, you can launch FOSPX PDF Editor from your application menu.

---

### 2. Manual Installation (For Developers or Those Who Want to Build from Source)

This method is suitable for users who want to run the application directly from source code or contribute to development.

> [!TIP]
> For a stable experience, use the **v1.8.3** tag when cloning. If you want to test the latest development changes, you can clone the `main` branch directly — but note that it may be less stable.

---

#### Pardus 23.4, Debian 12, and Ubuntu < 24.04 — Manual Installation

1.  **Install Required Dependencies:**
    Make sure the following packages are installed on your system. Run this command in your terminal:
    ```bash
    sudo apt update
    sudo apt install python3 python3-pip python3-venv \
                     python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository1.0-dev \
                     python3-numpy \
                     python3-dev libcairo2-dev build-essential \
                     fonts-noto-core fonts-liberation2
    ```
    *Optional (for DOCX export):*
    ```bash
    sudo apt install libreoffice-common
    ```

2.  **Download the Source Code:**

    **Recommended (stable v1.8.3):**
    ```bash
    git clone --branch 1.8.3 https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

    **For testing / latest development build (may be unstable):**
    ```bash
    git clone https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

3.  **Create and Activate a Virtual Environment (Recommended):**
    Creating a virtual environment in the project directory helps isolate Python dependencies from system-wide installations.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(You can use `deactivate` later to exit the virtual environment.)*

4.  **Install Python Dependencies:**
    Install the core Python library PyMuPDF (and numpy if not already installed from the system):
    ```bash
    pip install PyMuPDF numpy pygobject==3.50.0
    ```

5.  **Run the Application:**
    From the project root directory (where you extracted or cloned the source code), run:
    ```bash
    python3 run-editor.py
    ```

---

#### Ubuntu 24.04+, Debian 13 Trixie — Manual Installation

1.  **Install Required Dependencies:**
    ```bash
    sudo apt update
    sudo apt install python3 python3-pip python3-venv \
                     python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository-2.0-dev \
                     python3-numpy \
                     python3-dev libcairo2-dev build-essential \
                     fonts-noto-core fonts-liberation2
    ```
    *Optional (for DOCX export):*
    ```bash
    sudo apt install libreoffice-common
    ```

2.  **Download the Source Code:**

    **Recommended (stable v1.8.3):**
    ```bash
    git clone --branch 1.8.3 https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

    **For testing / latest development build (may be unstable):**
    ```bash
    git clone https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

3.  **Create and Activate a Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(You can use `deactivate` later to exit the virtual environment.)*

4.  **Install Python Dependencies:**
    ```bash
    pip install PyMuPDF numpy pygobject
    ```

5.  **Run the Application:**
    ```bash
    python3 run-editor.py
    ```

---

#### Arch Linux and Derivatives — Manual Installation

1.  **Download the Source Code:**

    **Recommended (stable v1.8.3):**
    ```bash
    git clone --branch 1.8.3 https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

    **For testing / latest development build (may be unstable):**
    ```bash
    git clone https://github.com/fospx-org/fospx-pdf-editor.git
    cd fospx-pdf-editor
    ```

    *Optional (for DOCX export):*
    ```bash
    sudo pacman -S libreoffice-fresh
    ```

2.  **Create and Activate a Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(You can use `deactivate` later to exit the virtual environment.)*

3.  **Install Python Dependencies:**
    ```bash
    pip install PyMuPDF numpy pygobject
    ```

4.  **Run the Application:**
    ```bash
    python3 run-editor.py
    ```

**CONGRATULATIONS! You have successfully launched FOSPX PDF Editor!**

---

#### Arch Linux and Derivatives — From AUR

1.  **Download, build and install:**

    ```bash
    git clone https://aur.archlinux.org/fospx-pdf-editor
    makepkg -sfi
    ```

    People using the yay AUR helper could build and install it using:
    
    ```bash
    yay -S fospx-pdf-editor
    ```
    
    or if you prefer to use paru:
    
    ```bash
    paru -S fospx-pdf-editor
    ```

    *Optional (for DOCX export):*
    ```bash
    sudo pacman -S libreoffice-fresh
    ```

2.  **Run the Application:**
    ```bash
    fospx-pdf-editor
    ```

    *You can also use your application launcher to execute FOSPX PDF Editor*

---

## Bug Reports and Feedback

If you encounter any bugs, have a feature request, or want to leave general feedback, please use the [**GitHub Issues**](https://github.com/fospx-org/fospx-pdf-editor/issues) section.

---

## Contributing

FOSPX PDF Editor is an open-source project and welcomes contributions! If you'd like to contribute, please follow these steps:

1.  Fork this repository.
2.  Create your own branch for a new feature or bug fix (`git checkout -b feature/new-feature` or `git checkout -b fix/bug-name`).
3.  Make your changes and commit them (`git commit -am 'Added new feature'`).
4.  Push your branch to GitHub (`git push origin feature/new-feature`).
5.  Open a Pull Request (PR).

---

## License

This project is licensed under the [**GNU General Public License v3.0 or later**](LICENSE).

---
