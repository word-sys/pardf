# pardf
Pardus PDF Editor / Pardus PDF Düzenleyicisi

#MilliTeknolojiHamlesi ve TEKNOFEST PARDUS UYGULAMA GELİŞTİRME ve PARDUS için üretilen ParDF, uzun zamandır eksikliğı hissedilen PDF Düzenleyicisi sıfırdan Pardus için tekrardan yapılıyor.

Linux PDF Düzenleyicisi

word-sys | Barın Güzeldemirci

# Nasıl Kurulur?

# Otomatik Kurulum

RELEASES bölümünden pardus-pdf-editor_0.1.0-1_all.deb indirin, ardından pardus-pdf-editor_0.1.0-1_all.deb i indirdiğiniz yere gelip Terminal açın

sudo apt install ./pardus-pdf-editor_0.1.0-1_all.deb

# Manual Kurulum

1.Adım RELEASES bölümünden son sürüm .zip veya .tar.xz indirin.

2.Adım .zip veya .tar.xz yi dışarı çıkartın.

3.Adım Sisteminizde python3-pip python3-venv python3-all-dev build-essential cmake libcairo2-dev libgirepository-2.0-dev paketinin yüklü olduğuna emin olun. Eğer yüklü değilse

sudo apt install python3-pip python3-venv python3-all-dev build-essential cmake libcairo2-dev libgirepository-2.0-dev

4.Adım Terminal'i açın:

python3 -m venv venv 

source venv/bin/activate 

pip install pygobject pillow pymupdf numpy
                        
5.Adım Dosyayı çıkarttığınız yerde python3 run-editor.py

TEBRİKLER UYGULAMAYI ÇALIŞTIRDINIZ!

# !Bilinen Problemler!
Kaydetme button'u PDF düzenlemeden sonra kaydetme fonksiyonunda hata veriyor. Düzeltilmeye çalışılıyor. Geçici çözüm ise PDF'i kaydetmek için Seçenekler (3 Çizgi) bölümünden 'PDF'i Dışa Aktar' seçeneğini kullanarak PDF'i kaydedebilirsiniz.

                        
