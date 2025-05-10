# ParDF - Pardus PDF Düzenleyicisi

**ParDF**, Pardus ve diğer Linux dağıtımları için geliştirilmiş, PDF dosyalarındaki metin içeriğini düzenlemeye odaklanan basit ve kullanıcı dostu bir araçtır. #MilliTeknolojiHamlesi ve TEKNOFEST ruhuyla, Pardus ekosistemindeki bir ihtiyacı karşılamak üzere sıfırdan geliştirilmiştir.

Geliştirici: **Barın Güzeldemirci (word-sys)**
Lisans: **GPL-3.0-or-later**
Proje Sayfası: [word-sys.github.io/pardf](https://word-sys.github.io/pardf) <!-- Web sitenizin linki -->
GitHub: [github.com/word-sys/pardf](https://github.com/word-sys/pardf)

---

## ⭐ Temel Özellikler

*   PDF dosyalarını açma ve görüntüleme
*   Sayfa içinde var olan metin bloklarını seçme
*   Seçili metinleri düzenleme veya silme
*   Sayfaya yeni metin blokları ekleme
*   Yazı tipi, boyutu ve rengini değiştirme
*   Düzenlenmiş PDF'leri kaydetme
*   PDF'leri DOCX (LibreOffice gerektirir) ve TXT formatlarında dışa aktarma
*   Kullanıcı dostu arayüz ve sayfa önizlemeleri

---

## 🚀 Kurulum

ParDF'i sisteminize kurmanın iki yolu vardır:

### 1. Otomatik Kurulum (Önerilen Yöntem)

Bu yöntem, Linux dağıtımları için en kolay kurulum yoludur.

1.  En son `.deb` paketini [**GitHub Releases**](https://github.com/word-sys/pardf/releases) sayfasından indirin. Genellikle `pardus-pdf-editor_X.Y.Z_all.deb` şeklinde bir dosya adı olacaktır (X.Y.Z sürüm numarasını temsil eder).
2.  Terminali, `.deb` dosyasını indirdiğiniz dizinde açın.
3.  Aşağıdaki komutu çalıştırarak paketi kurun:
    ```bash
    sudo apt update
    sudo apt install ./pardus-pdf-editor_X.Y.Z_all.deb
    ```
    *(Not: `pardus-pdf-editor_X.Y.Z_all.deb` yerine indirdiğiniz dosyanın tam adını yazın.)*
4.  Eğer kurulum sırasında bağımlılıklarla ilgili bir hata alırsanız, aşağıdaki komutu çalıştırarak eksik bağımlılıkları gidermeyi deneyin:
    ```bash
    sudo apt --fix-broken install
    ```
5.  Kurulum tamamlandıktan sonra ParDF'i uygulama menünüzden başlatabilirsiniz.

## !Pardus 23.4 ve Debian 12'de python3-fitz 1.21.1 sorunlu pakettir, paket güncellenene kadar ParDF DEB versiyonunda sorun yaşayabilirsiniz. Manuel kurulum tavsiye edilir!

### 2. Manuel Kurulum (Geliştiriciler veya Kaynaktan Derlemek İsteyenler İçin)

Bu yöntem, uygulamayı doğrudan kaynak kodundan çalıştırmak veya geliştirme yapmak isteyen kullanıcılar için uygundur.

### Pardus 23.4 ve Debian 12 için Manuel Kurulum

1.  **Gerekli Bağımlılıkları Kurun:**
    Öncelikle sisteminizde aşağıdaki paketlerin kurulu olduğundan emin olun. Terminale şu komutu yazarak kurabilirsiniz:
    ```bash
    sudo apt update
    sudo apt install python3 python3-pip python3-venv \
                     python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository1.0-dev\
                     python3-numpy \
                     python3-dev libcairo2-dev build-essential \
                     fonts-noto-core fonts-liberation2
    ```
    *İsteğe Bağlı (DOCX Dışa Aktarma İçin):*
    ```bash
    sudo apt install libreoffice-common
    ```

2.  **Kaynak Kodunu İndirin:**
    En son kaynak kodunu [**GitHub Releases**](https://github.com/word-sys/pardf/releases) sayfasından `.zip` veya `.tar.gz` (veya `.tar.xz`) formatında indirin ya da depoyu klonlayın:
    ```bash
    git clone https://github.com/word-sys/pardf.git
    cd pardf
    ```

3.  **Sanal Ortam Oluşturun ve Aktifleştirin (Önerilir):**
    Proje dizininde bir sanal ortam oluşturmak, Python bağımlılıklarını sistem genelindeki kurulumlardan izole etmenize yardımcı olur.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(Sanal ortamdan çıkmak için daha sonra `deactivate` komutunu kullanabilirsiniz.)*

4.  **Python Bağımlılıklarını Kurun:**
    Projenin temel Python kütüphanesi olan PyMuPDF'i (ve numpy'ı, eğer sistemden kurulmadıysa) kurun:
    ```bash
    pip install PyMuPDF numpy pygobject==3.50.0
    ```

5.  **Uygulamayı Çalıştırın:**
    Proje ana dizinindeyken (kaynak kodlarını çıkarttığınız veya klonladığınız yerde) aşağıdaki komutu çalıştırın:
    ```bash
    python3 run-editor.py
    ```

### Diğer Dağıtımlar (Ubuntu, Fedora, Arch vb.) Manuel Kurulum

1.  **Gerekli Bağımlılıkları Kurun:**
    Öncelikle sisteminizde aşağıdaki paketlerin kurulu olduğundan emin olun. Terminale şu komutu yazarak kurabilirsiniz:
    ```bash
    sudo apt update
    sudo apt install python3 python3-pip python3-venv \
                     python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository2.0-dev\
                     python3-numpy \
                     python3-dev libcairo2-dev build-essential \
                     fonts-noto-core fonts-liberation2
    ```
    *İsteğe Bağlı (DOCX Dışa Aktarma İçin):*
    ```bash
    sudo apt install libreoffice-common
    ```

2.  **Kaynak Kodunu İndirin:**
    En son kaynak kodunu [**GitHub Releases**](https://github.com/word-sys/pardf/releases) sayfasından `.zip` veya `.tar.gz` (veya `.tar.xz`) formatında indirin ya da depoyu klonlayın:
    ```bash
    git clone https://github.com/word-sys/pardf.git
    cd pardf
    ```

3.  **Sanal Ortam Oluşturun ve Aktifleştirin (Önerilir):**
    Proje dizininde bir sanal ortam oluşturmak, Python bağımlılıklarını sistem genelindeki kurulumlardan izole etmenize yardımcı olur.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    *(Sanal ortamdan çıkmak için daha sonra `deactivate` komutunu kullanabilirsiniz.)*

4.  **Python Bağımlılıklarını Kurun:**
    Projenin temel Python kütüphanesi olan PyMuPDF'i (ve numpy'ı, eğer sistemden kurulmadıysa) kurun:
    ```bash
    pip install PyMuPDF numpy pygobject
    ```

5.  **Uygulamayı Çalıştırın:**
    Proje ana dizinindeyken (kaynak kodlarını çıkarttığınız veya klonladığınız yerde) aşağıdaki komutu çalıştırın:
    ```bash
    python3 run-editor.py
    ```

🎉 **TEBRİKLER! ParDF'i başarıyla çalıştırdınız!** 🎉

---

## 🐛 Hata Bildirimi ve Geri Bildirim

Herhangi bir hata ile karşılaşırsanız, bir özellik talebiniz varsa veya genel bir geri bildirimde bulunmak isterseniz, lütfen [**GitHub Issues**](https://github.com/word-sys/pardf/issues) bölümünü kullanın.

---

## 🤝 Katkıda Bulunma

ParDF açık kaynaklı bir projedir ve katkılarınıza açıktır! Katkıda bulunmak isterseniz, lütfen aşağıdaki adımları izleyin:

1.  Bu depoyu forklayın.
2.  Yeni bir özellik veya hata düzeltmesi için kendi dalınızı oluşturun (`git checkout -b ozellik/yeni-ozellik` veya `git checkout -b duzeltme/hata-adi`).
3.  Değişikliklerinizi yapın ve commit edin (`git commit -am 'Yeni özellik eklendi'`).
4.  Dalınızı GitHub'a itin (`git push origin ozellik/yeni-ozellik`).
5.  Bir Pull Request (PR) oluşturun.

---

## 📜 Lisans

Bu proje [**GNU General Public License v3.0 or later**](LICENSE) altında lisanslanmıştır.

---
