# pardf
Pardus PDF Editor / Pardus PDF Düzenleyicisi

#MilliTeknolojiHamlesi ve TEKNOFEST PARDUS UYGULAMA GELİŞTİRME ve PARDUS için üretilen ParDF, uzun zamandır eksikliğı hissedilen PDF Düzenleyicisi sıfırdan Pardus için tekrardan yapılıyor.

Linux PDF Düzenleyicisi

word-sys | Barın Güzeldemirci

# Nasıl Kurulur?

# Otomatik Kurulum

RELEASES bölümünden pardus-pdf-editor_0.1.0-3_all.deb indirin, ardından pardus-pdf-editor_0.1.0-3_all.deb i indirdiğiniz yere gelip Terminal açın

sudo apt install ./pardus-pdf-editor_0.1.0-3_all.deb

# Manual Kurulum

1.Adım RELEASES bölümünden son sürüm .zip .tar.xz veya Source Code indirin.

2.Adım .zip veya .tar.xz yi dışarı çıkartın.

3.Adım Sisteminizde python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-numpy python3-fitz python3-dev libcairo2-dev build-essential libreoffice-common fonts-dejavu-core paketinin yüklü olduğuna emin olun. Eğer yüklü değilse

sudo apt update && sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-numpy python3-fitz python3-dev libcairo2-dev build-essential libreoffice-common fonts-dejavu-core

4.Adım Terminal'i açın:
                        
Dosyayı çıkarttığınız yerde python3 run-editor.py

TEBRİKLER UYGULAMAYI ÇALIŞTIRDINIZ!

                        
