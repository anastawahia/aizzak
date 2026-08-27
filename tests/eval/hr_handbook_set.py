"""``P-38``'s evaluation set, as data (decision س-22 — the one item in the
port-fidelity audit that waited on an input from the owner rather than on a
decision or on work).

**Provenance, and the line between what is the owner's and what is not.**

* ``QUESTIONS`` — the 15 questions and their reference answers are the
  OWNER'S, verbatim from ``docs/hr-quiz-en.md`` (English) and
  ``docs/hr-quiz.md`` (Arabic), over ``docs/hr-no-table.docx``. They are not
  paraphrased here and must not be: a calibration set whose questions drifted
  from the ones somebody actually vouched for is a set nobody vouched for.
  The two languages are the SAME 15 questions, which is what makes the
  cross-lingual half of every measurement a controlled comparison rather than
  a second, unrelated set.
* ``gold`` — NOT invented either, and not a judgement. Each entry is a regular
  expression that a chunk of the INDEXED corpus either contains or does not,
  so "did retrieval deliver the answer" is answered by the document rather
  than asserted about it. Every pattern was read off a real chunk first, and
  several questions have more than one because the handbook states the same
  fact twice — once in a quick-reference table and once in the prose body.
  ⚠️ Match them against the DELIVERED CONTEXT, not against the delivered chunk
  ids: parent expansion (``P-34``) replaces a matched leaf's text with its
  parent's, so the fact routinely arrives inside a candidate that is not
  itself a gold leaf. Measuring on ids alone under-reports recall badly.
* ``NEGATIVES`` — questions this corpus provably cannot answer. **These are
  not the owner's**, and they carry no reference answer, because there is
  nothing to reference. They exist because a floor is a two-sided instrument
  and 15 answerable questions only ever measure one of its sides: without
  them "reject nothing" and "reject exactly the right things" are the same
  measurement. Each was checked against the indexed text before being trusted
  as unanswerable — ``n6`` in particular is the useful one, lexically adjacent
  to the engineering document sharing the space ("chloride" appears there,
  but only inside "Polyvinyl Chloride"), so it probes the sparse leg the way
  the easy negatives cannot.

The corpus the numbers in docs/rag-fidelity-audit.md §4-و were measured on is
ONE space holding two documents: the handbook (221 chunks, 23 parents) and an
unrelated electrical design criteria PDF (811 chunks). The second is not
noise in the experiment — it is the distractor that makes "did the answer come
from the right document" a question with an answer.
"""

from __future__ import annotations

QUESTIONS: list[dict[str, object]] = [
    {
        "id": 1,
        "en": "What is the maximum number of working hours allowed per week?",
        "ar": "ما هو الحد الأقصى لساعات العمل الأسبوعية المسموح بها؟",
        "answer": "48 hours per week",
        "gold": [r"Max 48h/week", r"forty-eight \(48\) hours per week", r"Max 48 hours/week"],
    },
    {
        "id": 2,
        "en": "What is the daily grace period allowed for lateness without a deduction?",
        "ar": "ما هي فترة السماح اليومية المسموح بها للتأخير عن العمل دون خصم؟",
        "answer": "up to 10 minutes",
        # RUF001: the curly apostrophe is the one the handbook actually uses;
        # the class accepts either so the pattern survives a re-export that
        # straightens it. A "safer" ASCII-only pattern would simply not match.
        "gold": [
            r"10 minutes[’'] daily grace",  # noqa: RUF001
            r"delay of up to 10 minutes is allowed",
        ],
    },
    {
        "id": 3,
        "en": (
            "What is the maximum daily Early-Out duration using hourly leave, "
            "and how much does it become during Ramadan?"
        ),
        "ar": (
            "ما هو الحد الأقصى لساعات الخروج المبكر (Early-Out) باستخدام الإجازة "
            "بالساعة، وكم تصبح في شهر رمضان؟"
        ),
        "answer": "4 hours/day, 3 in Ramadan",
        "gold": [
            r"Up to 4 hours/day \(3 hours in Ramadan\)",
            r"cannot exceed four \(4\) hours per day \(3 hours in Ramadan",
        ],
    },
    {
        "id": 4,
        "en": "Within how many hours must a medical report be submitted for sick leave?",
        "ar": "خلال كم ساعة يجب تقديم التقرير الطبي في حالة الإجازة المرضية؟",
        "answer": "within 24 hours",
        "gold": [
            r"Medical report within 24h",
            r"medical report within 24h",
            r"medical report should be submitted within 24 hours",
        ],
    },
    {
        "id": 5,
        "en": "What is the overtime pay multiplier for regular weekdays?",
        "ar": "ما هو معامل احتساب أجر العمل الإضافي (Overtime) في أيام الأسبوع العادية؟",
        "answer": "1.25x",
        "gold": [r"Weekday; Rate: 1\.25x", r"normal working days shall be 1 25 times"],
    },
    {
        "id": 6,
        "en": "What is the overtime pay multiplier for weekends or public holidays?",
        "ar": "ما هو معامل احتساب أجر العمل الإضافي في عطلة نهاية الأسبوع أو العطل الرسمية؟",
        "answer": "1.50x",
        "gold": [
            r"Weekend/Public Holiday; Rate: 1\.50x",
            r"weekend or on official or religious holidays shall be 1 50 times",
        ],
    },
    {
        "id": 7,
        "en": (
            "What is the payroll cut-off date for submitting overtime claims to be "
            "included in the same month's payroll?"
        ),
        "ar": (
            "ما هو الموعد النهائي (Payroll Cut-off) لتقديم مطالبات العمل الإضافي "
            "لإدراجها في رواتب نفس الشهر؟"
        ),
        "answer": "the 15th of each month",
        "gold": [r"Payroll cut-off: 15th of each month"],
    },
    {
        "id": 8,
        "en": (
            "What percentage of an employee's basic salary is deducted for their "
            "Social Security contribution?"
        ),
        "ar": "ما هي نسبة اشتراك الموظف في الضمان الاجتماعي من راتبه الأساسي؟",
        "answer": "7.5%",
        "gold": [r"Employee 7\.5%", r"bear 7 50% of their basic salary"],
    },
    {
        "id": 9,
        "en": "What percentage does the company (employer) contribute to Social Security?",
        "ar": "ما هي نسبة اشتراك الشركة (صاحب العمل) في الضمان الاجتماعي؟",
        "answer": "14.25% / 15.25%",
        # RUF001: an EN DASH, because that is the character in the indexed
        # text. Normalising it to a hyphen here would make the pattern match
        # nothing and report a working retrieval as a miss.
        "gold": [r"Company 14\.25–15\.25%", r"bear 14 25% \(non-hazardous"],  # noqa: RUF001
    },
    {
        "id": 10,
        "en": (
            "When is the 13th salary paid, and what condition is required to receive it in full?"
        ),
        "ar": "متى يُصرف الراتب الثالث عشر (13th Salary)، وما الشرط المرتبط بصرفه كاملاً؟",
        "answer": "June; 12 months of service",
        "gold": [r"13th salary \(June\)", r"In June, for staff with 12 months of service"],
    },
    {
        "id": 11,
        "en": ("What is the Annual Aggregate Limit of health insurance coverage for Class A?"),
        "ar": (
            "ما هو الحد الأقصى السنوي للتغطية التأمينية الصحية الإجمالية "
            "(Annual Aggregate Limit) للفئة A؟"
        ),
        "answer": "JOD 200,000",
        "gold": [r"Annual Aggregate Limit; Details: JOD 200,000"],
    },
    {
        "id": 12,
        "en": "How many outpatient visits per member per year are covered under health insurance?",
        "ar": (
            "كم عدد الزيارات الخارجية (Outpatient Visits) المغطاة لكل فرد مؤمَّن "
            "عليه سنوياً في التأمين الصحي؟"
        ),
        "answer": "14 visits",
        "gold": [r"Outpatient Visits; Details: 14 per member per year"],
    },
    {
        "id": 13,
        "en": (
            "What is the waiting period required before coverage applies to Tonsils & "
            "Adenoids, Deviated septum, Sinusitis, and Hernias?"
        ),
        "ar": (
            "ما هي مدة فترة الانتظار (Waiting Period) المطلوبة قبل تغطية حالات "
            "اللوزتين، انحراف الحاجز الأنفي، الجيوب الأنفية، والفتق؟"
        ),
        "answer": "6 months",
        "gold": [
            r"Tonsils & Adenoids; Deviated septum; Sinusitis; Hernias; Waiting Period: 6 months"
        ],
    },
    {
        "id": 14,
        "en": (
            "What is the minimum number of candidates the Line Manager must shortlist "
            "for interviews in the recruitment process?"
        ),
        "ar": (
            "ما هو الحد الأدنى لعدد المرشحين الذين يجب على مدير الخط ترشيحهم لإجراء "
            "المقابلات في عملية التوظيف؟"
        ),
        "answer": "five (5)",
        "gold": [r"minimum of five \(5\) candidates"],
    },
    {
        "id": 15,
        "en": (
            "Within how many working days must a transportation reimbursement claim be "
            "submitted with supporting receipts?"
        ),
        "ar": "خلال كم يوم عمل يجب تقديم طلب استرداد بدل المواصلات مع الإيصالات المؤيدة؟",
        "answer": "5 working days",
        "gold": [
            r"Submit receipts/justifications within 5 working days",
            r"submit receipts & justification within 5 working days",
            r"Within 5 working days of the travel date",
            r"within 5 working days via the HR system",
        ],
    },
]

# Not the owner's, and deliberately marked so. A floor is a two-sided
# instrument and 15 answerable questions only ever measure one side of it.
NEGATIVES: list[dict[str, str]] = [
    {
        "id": "n1",
        "en": "What is the melting point of tungsten in degrees Celsius?",
        "ar": "ما هي درجة انصهار التنجستن بالدرجة المئوية؟",
    },
    {
        "id": "n2",
        "en": "Who won the 1998 FIFA World Cup final?",
        "ar": "من فاز بنهائي كأس العالم لكرة القدم عام 1998؟",
    },
    {
        "id": "n3",
        "en": "How do I renew my passport at the civil status department?",
        "ar": "كيف أجدد جواز سفري في دائرة الأحوال المدنية؟",
    },
    {
        "id": "n4",
        "en": "What is the company's policy on cryptocurrency trading bonuses?",
        "ar": "ما هي سياسة الشركة بشأن مكافآت تداول العملات الرقمية؟",
    },
    {
        "id": "n5",
        "en": "How many parking spaces does the Amman head office have?",
        "ar": "كم عدد مواقف السيارات في المكتب الرئيسي في عمّان؟",
    },
    {
        "id": "n6",
        "en": "What is the maximum allowable chloride content in the concrete mix?",
        "ar": "ما هو الحد الأقصى المسموح به لمحتوى الكلوريد في الخلطة الخرسانية؟",
    },
]
