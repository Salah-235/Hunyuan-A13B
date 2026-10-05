<div dir="rtl">

# مساعد الوثائق البنكية

تطبيق ويب يعمل على **الهاتف والكمبيوتر**، يشتغل بطريقة شبيهة بـ **Google NotebookLM**: يضيف المسؤول ملفات البنك (القوانين الداخلية، شروط المنتجات، القروض، التعريفات…)، ثم يطرح الموظفون أسئلتهم فيجيب المساعد **من هذه الملفات فقط**، ويذكر **المصدر ورقم الصفحة** لكل معلومة.

- يبدأ التطبيق **فارغاً**، وأنت من يضيف الوثائق.
- **سري**: الدخول بكلمة مرور، ولا يدخل إلا الموظفون الذين تنشئ لهم حساباً.
- واجهة **عربية وفرنسية** مع زر للتبديل بينهما، والإجابة تكون بلغة السؤال.
- يُثبَّت على الهاتف كتطبيق (أيقونة على الشاشة الرئيسية، ملء الشاشة) دون المرور بمتجر التطبيقات.

> لقطات الشاشة (التُقطت بنموذج تجريبي وهمي، لذلك نص الإجابة فيها مجرد مثال): [تسجيل الدخول](docs/screenshots/login.png) · [المحادثة على الكمبيوتر](docs/screenshots/chat-desktop.png) · [المحادثة على الهاتف](docs/screenshots/chat-phone.png) · [الواجهة الفرنسية](docs/screenshots/chat-phone-fr.png) · [إدارة الوثائق](docs/screenshots/admin-documents.png) · [إدارة المستخدمين](docs/screenshots/admin-users.png)

---

## المميزات

| الميزة | التفاصيل |
|---|---|
| أنواع الملفات | PDF، Word ‏(.docx)، Excel ‏(.xlsx)، CSV، نص (.txt / .md) |
| ملفات PDF الممسوحة ضوئياً | قراءة آلية (OCR) للصفحات التي هي صور: Tesseract على الخادم نفسه، أو نموذج رؤية أدق في الأرقام. المقاطع المقروءة آلياً تحمل شارة «OCR» وينبّه المساعد إلى التحقق من أرقامها |
| إجابة شاملة | زر «إجابة شاملة» تحت خانة السؤال: يقرأ 24 مقطعاً بدل 8 ويجمع كل العناصر من كل المصادر — للأسئلة العامة والقوائم (كل الوثائق المطلوبة، كل الرسوم…) |
| تلخيص وثيقة كاملة | زر التلخيص بجانب كل وثيقة: ملخص منظم للوثيقة كلها مع الأرقام وأرقام الصفحات. الوثائق الطويلة تُقرأ على أجزاء ثم تُجمع، والملخص يُحفظ فلا يُعاد حسابه |
| دقة الإجابة | إجابة مبنية على المقاطع المسترجعة فقط، مع أرقام المصادر [1] [2]. إذا لم تكن المعلومة في الوثائق يقول ذلك صراحة ولا يخترع |
| ملفات PDF العربية | إعادة ترتيب النص العربي حسب موضع الحروف في الصفحة، حتى تبقى الأرقام والنسب في مكانها الصحيح (مثل «المادة 3» و«30%») |
| الجداول | صفوف جداول PDF (العربية والفرنسية) تبقى في سطر واحد بخلايا مرتبة `|`، وجداول Word تحافظ على مكان كل قيمة في عمودها حتى مع الخلايا الفارغة |
| فهم بنية الوثيقة | التعرف على «الفصل / الباب / المادة / Article / Chapitre» وإرفاقها بكل مقطع |
| البحث | بحث بالكلمات (BM25) مع معالجة العربية: الهمزات والتشكيل والتاء المربوطة وأداة التعريف. ويمكن إضافة بحث دلالي متعدد اللغات |
| أسئلة المتابعة | يعيد النموذج صياغة السؤال اعتماداً على المحادثة، ويترجمه إلى العربية أو الفرنسية للبحث في وثائق اللغتين |
| اختيار المصادر | مثل NotebookLM: يمكن حصر الإجابة في وثائق أو تصنيفات معينة |
| فتح المصدر | زر «فتح الوثيقة» يفتح ملف PDF على الصفحة المذكورة مباشرة |
| الصلاحيات | **مسؤول**: يدير الوثائق والمستخدمين. **موظف**: يسأل ويطلع على المصادر |
| سجل النشاط | تسجيل الدخول، المحاولات الفاشلة، الأسئلة، رفع الوثائق وحذفها، فتح الملفات |
| الأمان | تشفير كلمات المرور (scrypt)، قفل مؤقت بعد 5 محاولات خاطئة، انتهاء الجلسة بعد الخمول، حماية CSRF وCSP، والملفات لا تُعرض إلا لمن سجّل الدخول |

---

## كيف يعمل؟

```
رفع الملف ← استخراج النص ← تقسيمه إلى مقاطع (مع الفصل/المادة ورقم الصفحة) ← فهرسة
سؤال الموظف ← إعادة صياغة وترجمة ← بحث هجين ← أفضل 8 مقاطع ← نموذج الذكاء الاصطناعي ← إجابة مع المصادر
```

النموذج المقترح هو **Hunyuan-A13B** الموجود في هذا المستودع، ويعمل على خوادم البنك نفسها، فلا تخرج أي وثيقة إلى الإنترنت. ويعمل التطبيق أيضاً مع أي خادم متوافق مع OpenAI API ‏(vLLM أو SGLang أو Ollama…).

---

## التشغيل السريع

### 1) تثبيت التطبيق

يتطلب Python 3.10 أو أحدث (Windows أو Linux أو macOS):

```bash
cd bank_assistant
python -m venv .venv
# Windows:  .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # ثم عدّل عنوان خادم الذكاء الاصطناعي
python manage.py run --port 8080
```

### 2) التهيئة الأولى

عند أول تشغيل يظهر في نافذة الخادم **رمز تهيئة** مثل `A1B2-C3D4-E5F6`. افتح المتصفح على العنوان `http://عنوان-الخادم:8080`، وأدخل الرمز ثم أنشئ حساب المسؤول.

### 3) الاستعمال

1. **لوحة الإدارة ← الوثائق**: اسحب الملفات وأفلتها. يمكنك كتابة تصنيف لها (مثلاً: القروض أو البطاقات).
2. **لوحة الإدارة ← المستخدمون**: أنشئ حساباً لكل موظف، ثم سلّمه اسم المستخدم وكلمة المرور.
3. يدخل الموظف من هاتفه أو حاسوبه ويطرح سؤاله.

---

## ربط نموذج الذكاء الاصطناعي

### الخيار أ: Hunyuan-A13B على خوادم البنك (مستحسن للسرية)

انظر قسم النشر في [README المستودع](../README.md). مثال بـ vLLM:

```bash
docker run --privileged --user root --net=host --ipc=host -v ~/.cache:/root/.cache/ \
  --gpus=all -it --entrypoint python hunyuaninfer/hunyuan-a13b:hunyuan-moe-A13B-vllm \
  -m vllm.entrypoints.openai.api_server --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size 4 --model tencent/Hunyuan-A13B-Instruct --trust-remote-code
```

ثم ضع في ملف `.env`:

```ini
LLM_BASE_URL=http://عنوان-خادم-النموذج:8000/v1
LLM_MODEL=tencent/Hunyuan-A13B-Instruct
```

> **ملاحظة عن العتاد:** النسخة الكاملة (BF16) تعمل في المثال أعلاه على 4 بطاقات GPU، ونسخة **Int4** ‏(`tencent/Hunyuan-A13B-Instruct-GPTQ-Int4`) تحتاج عتاداً أقل (انظر `inference/run_server_int4.sh`).
> يتعامل التطبيق تلقائياً مع وضع «التفكير» في Hunyuan، فيُخفي مرحلة التفكير ويعرض الإجابة فقط. وللإجابة أسرع وبدقة أقل قليلاً اضبط `LLM_THINKING=0`.

### الخيار ب: Ollama (جهاز واحد، أبسط)

```bash
ollama pull qwen2.5:14b
```

```ini
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:14b
```

### البحث الدلالي متعدد اللغات (اختياري، يرفع الدقة)

يسمح لسؤال بالعربية أن يجد فقرة مكتوبة بالفرنسية، ويفهم المرادفات. مثال باستعمال Ollama:

```bash
ollama pull bge-m3
```

```ini
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODEL=bge-m3
```

بعد تفعيله شغّل `python manage.py reindex` مرة واحدة لفهرسة الوثائق الموجودة من قبل.

من لوحة الإدارة ← **النظام** يمكنك التحقق من الإعدادات والضغط على «اختبار الاتصال».

### قراءة ملفات PDF الممسوحة ضوئياً (OCR)

يقرأ التطبيق تلقائياً كل صفحة PDF لا تحتوي على نص (صورة ممسوحة). هناك طريقتان:

1. **Tesseract** (افتراضي، على الخادم نفسه، لا تخرج الصفحات منه). ثبّته مع اللغة العربية والفرنسية:

   ```bash
   sudo apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-fra tesseract-ocr-eng   # Ubuntu / Debian
   ```

   في Windows ثبّت [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) واختر Arabic وFrench أثناء التثبيت. صورة Docker تحتوي عليه مسبقاً.
   يحوّل التطبيق كل صفحة إلى أبيض وأسود قبل القراءة، ويقرأ الصفحة مرة ثانية لاستعادة علامات «%» التي يخطئ فيها Tesseract عادة بجانب النص العربي.

2. **نموذج رؤية** (أدق في الأرقام والجداول): نموذج يقرأ الصور يعمل داخل البنك، مثل Qwen2.5-VL عبر vLLM:

   ```ini
   OCR_ENGINE=vision
   OCR_VISION_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
   OCR_VISION_BASE_URL=http://localhost:8001/v1
   ```

بعد تثبيت OCR اضغط «إعادة المعالجة» على الوثائق الممسوحة الموجودة من قبل. لوحة الإدارة ← **النظام** تعرض طريقة القراءة المفعلة.

---

## الاستعمال على الهاتف (تطبيق جوال)

1. شغّل التطبيق على خادم في شبكة البنك، ويستحسن تشغيله عبر **HTTPS** (انظر الفقرة التالية).
2. يفتح الموظف العنوان في متصفح الهاتف ويسجّل دخوله.
3. **Android ‏(Chrome)**: القائمة ⋮ ثم «تثبيت التطبيق» أو «إضافة إلى الشاشة الرئيسية»، ويظهر أيضاً زر «تثبيت التطبيق» في قائمة المستخدم.
   **iPhone ‏(Safari)**: زر المشاركة ثم «إضافة إلى الشاشة الرئيسية».

بعدها يفتح كتطبيق مستقل بأيقونته الخاصة. ولا يحفظ الهاتف أي وثيقة أو إجابة في ذاكرة التخزين المؤقت.

---

## النشر عبر HTTPS (مستحسن)

تثبيت التطبيق على الهاتف يتطلب HTTPS، وHTTPS يحمي كلمات المرور داخل الشبكة. ضع التطبيق خلف خادم وكيل، ثم اضبط في `.env`:

```ini
COOKIE_SECURE=1
TRUST_PROXY=1
```

**Caddy** (يصدر الشهادة تلقائياً، أو `tls internal` لشبكة داخلية):

```
assistant.bank.local {
    tls internal
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

**nginx**:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_buffering off;              # لعرض الإجابة أثناء كتابتها
    proxy_read_timeout 600s;
    client_max_body_size 200m;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### Docker

```bash
cp .env.example .env
docker compose up -d app                # التطبيق فقط
docker compose --profile gpu up -d      # التطبيق + Hunyuan-A13B على بطاقات NVIDIA
docker compose logs app                 # لرؤية رمز التهيئة
```

يتطلب Docker Compose الإصدار 2.24 أو أحدث، وتُحفظ البيانات في volume باسم `bank-data`.

---

## الأوامر

| الأمر | الوظيفة |
|---|---|
| `python manage.py run --port 8080` | تشغيل الخادم |
| `python manage.py create-admin` | إنشاء حساب مسؤول من سطر الأوامر |
| `python manage.py reset-password USERNAME` | تعيين كلمة مرور جديدة (مثلاً إذا نسي المسؤول كلمته) |
| `python manage.py reindex` | إعادة معالجة كل الوثائق |

## النسخ الاحتياطي

كل البيانات في مجلد `data/`: قاعدة البيانات `app.db`، والملفات الأصلية `files/`، ومفتاح الجلسات `secret_key`. انسخ هذا المجلد بانتظام إلى مكان آمن، ولا تشاركه مع أحد.

## ملاحظات مهمة عن الدقة

- ملفات PDF **الممسوحة ضوئياً** تُقرأ آلياً (OCR)، لكن القراءة الآلية قد تخطئ في بعض الأرقام والنسب (مثلاً «90%» قد تُقرأ «9090»). لذلك تظهر على هذه المقاطع شارة «OCR» وينبّه المساعد إلى مراجعة الأرقام في الصفحة الأصلية. الأفضل دائماً استعمال النسخة النصية الأصلية للملف (PDF مصدَّر من Word مثلاً).
- الدقة النهائية تعتمد على نموذج الذكاء الاصطناعي المستعمل: النماذج الأكبر مثل Hunyuan-A13B أدق من النماذج الصغيرة.
- يعرض التطبيق المصدر مع كل إجابة. يجب على الموظف دائماً التحقق من النص الأصلي قبل أي قرار يخص الزبون.

## نسخة تجريبية داخل Claude

المجلد `web_demo/` يحتوي على نسخة من الصفحة تعمل داخل Claude للتجربة السريعة دون خادم (انظر `web_demo/README.md`).

## الاختبارات

```bash
pip install pytest
python -m pytest tests -q
```

تشمل الاختبارات استخراج PDF عربي حقيقي، وقراءة PDF ممسوح ضوئياً (OCR)، والبحث بالعربية والفرنسية، والإجابة المتدفقة، والإجابة الشاملة، وتلخيص الوثائق، والصلاحيات، والقفل بعد المحاولات الخاطئة، وحماية CSRF. ويحتوي الملف `tests/fake_llm.py` على خادم نموذج وهمي للتجربة دون GPU:

```bash
python tests/fake_llm.py 8001 &
LLM_BASE_URL=http://127.0.0.1:8001/v1 python manage.py run
```

</div>

---

## Résumé en français

**Assistant documentaire bancaire** : une application web (ordinateur + mobile, installable comme une application) qui fonctionne comme NotebookLM. L'administrateur ajoute les documents de la banque (règlements, produits, crédits, tarifs) ; les employés posent leurs questions et l'assistant répond **uniquement à partir de ces documents**, en citant la **source et la page**.

- Interface **arabe / français**, réponses dans la langue de la question.
- Accès **confidentiel** par mot de passe ; seuls les comptes créés par l'administrateur peuvent se connecter. Journal d'activité complet.
- Formats : PDF (y compris l'arabe, avec remise en ordre du texte bidirectionnel), Word, Excel, CSV, texte.
- Recherche hybride (mots-clés + sémantique multilingue en option), reformulation et traduction automatique des questions.
- **PDF scannés** : OCR automatique des pages-images (Tesseract sur le serveur, ou un modèle de vision plus précis sur les chiffres). Les extraits issus de l'OCR portent un badge « OCR » et l'assistant invite à vérifier leurs chiffres.
- **Réponse complète** : un interrupteur sous la zone de question fait lire 24 extraits au lieu de 8, pour les listes et les questions générales.
- **Résumé d'un document entier** : bouton à côté de chaque document ; les longs documents sont lus par parties puis combinés, et le résumé est enregistré.
- Modèle recommandé : **Hunyuan-A13B** hébergé sur les serveurs de la banque (vLLM), ou tout serveur compatible OpenAI (Ollama…).

Démarrage : `pip install -r requirements.txt`, `cp .env.example .env`, `python manage.py run`, puis ouvrez `/setup` avec le code affiché dans la console.
