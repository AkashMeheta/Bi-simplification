
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter


# =====================================================
# API CONFIGURATION
# =====================================================

API_KEY = "YOUR_API_KEY"
BASE_URL = "https://your-api-url/v1"
MODEL_NAME = "your-model-name"


# =====================================================
# LOAD INSURANCE POLICY AUTOMATICALLY
# =====================================================

with open("policy.txt", "r", encoding="utf-8") as file:
    policy = file.read()


# =====================================================
# CHUNKING
# =====================================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=200
)

chunks = splitter.split_text(policy)

print(f"\nPolicy loaded successfully.")
print(f"Number of chunks: {len(chunks)}")


# =====================================================
# LLM
# =====================================================

llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0
)


# =====================================================
# USER INPUT
# =====================================================

print("\n==========================================")
print(" PRIOR AUTHORIZATION ASSISTANT")
print("==========================================")

treatment = input("\nTreatment / Procedure: ")
diagnosis = input("Diagnosis / Reason: ")
patient_type = input("Patient Type (New/Existing): ")


# =====================================================
# ANALYZE POLICY CHUNKS
# =====================================================

findings = []

for i, chunk in enumerate(chunks):

    prompt = f"""
You are a healthcare insurance Prior Authorization assistant.

A patient is requesting the following treatment:

Treatment:
{treatment}

Diagnosis / Reason:
{diagnosis}

Patient Type:
{patient_type}

Analyze the insurance policy section below.

Extract information related to:

1. Covered treatments and procedures
2. Non-covered services
3. Exclusions
4. Waiting periods
5. Prior authorization requirements

Only use information explicitly present in the policy.
Do not make assumptions or invent coverage.

Policy Section:
{chunk}
"""

    response = llm.invoke(prompt)
    findings.append(response.content)

    print(f"Analyzing policy section {i + 1}/{len(chunks)}")


# =====================================================
# FINAL STRUCTURED SUMMARY
# =====================================================

policy_findings = "\n\n".join(findings)

final_prompt = f"""
You are a healthcare insurance Prior Authorization assistant.

Generate a structured Prior Authorization Summary for:

Treatment / Procedure:
{treatment}

Diagnosis / Reason:
{diagnosis}

Patient Type:
{patient_type}

Use ONLY the policy findings provided below.

Do not invent or assume coverage.

Use "Not specified in the policy" when information is unavailable.

Return the following structure:

PRIOR AUTHORIZATION SUMMARY

Treatment / Procedure:
{treatment}

Diagnosis / Reason:
{diagnosis}

Coverage Status:
<Covered / Not Covered / Partially Covered / Not Specified>

1. Covered Treatments & Procedures
- ...

2. Non-Covered Services
- ...

3. Exclusions
- ...

4. Waiting Periods
- ...

5. Prior Authorization Requirement
- Required / Not Required / Not Specified
- Conditions, if applicable

6. Important Policy Conditions
- ...

7. Decision Notes
- Mention any additional information that may be required
  before authorization can be confirmed.

Policy Findings:
{policy_findings}
"""

final_response = llm.invoke(final_prompt)


# =====================================================
# DISPLAY RESULT
# =====================================================

print("\n")
print("=" * 60)
print("FINAL PRIOR AUTHORIZATION SUMMARY")
print("=" * 60)

print(final_response.content)



Healthcare Insurance Policy

The policy covers medically necessary hospitalization,
inpatient surgery, and diagnostic procedures when medically
required.

Covered procedures include cardiac surgery, orthopedic
surgery, MRI scans and CT scans.

Cosmetic surgery is not covered unless medically necessary
due to an accident.

Dental treatment is not covered under this policy.

Pre-existing conditions are subject to a waiting period of
24 months.

Maternity-related hospitalization is subject to a waiting
period of 12 months.

Prior authorization is required for planned hospitalization,
major surgeries, MRI scans, CT scans and expensive specialty
medications.

Emergency hospitalization does not require prior
authorization, but the insurance company must be notified
within 48 hours.

Experimental treatments and non-medically necessary
procedures are excluded from coverage.

API_KEY = "YOUR_GROQ_API_KEY"

BASE_URL = "https://api.groq.com/openai/v1"

MODEL_NAME = "llama-3.3-70b-versatile"

