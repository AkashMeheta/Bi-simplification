from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
import os


# =====================================================
# API CONFIGURATION
# =====================================================

API_KEY = "YOUR_API_KEY"
BASE_URL = "https://your-api-url/v1"
MODEL_NAME = "your-model-name"

# Embedding model supported by your API
EMBEDDING_MODEL = "your-embedding-model"


# =====================================================
# VECTOR DB CONFIGURATION
# =====================================================

VECTOR_DB_PATH = "./faiss_policy_db"


# =====================================================
# LOAD INSURANCE POLICY
# =====================================================

with open("policy.txt", "r", encoding="utf-8") as file:
    policy = file.read()

print("\nPolicy loaded successfully.")


# =====================================================
# CHUNKING
# =====================================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=200
)

chunks = splitter.split_text(policy)

print(f"Number of chunks: {len(chunks)}")


# =====================================================
# CREATE DOCUMENTS
# =====================================================

documents = []

for i, chunk in enumerate(chunks):

    documents.append(
        Document(
            page_content=chunk,
            metadata={
                "source": "policy.txt",
                "chunk_id": i
            }
        )
    )


# =====================================================
# EMBEDDINGS
# =====================================================

embeddings = OpenAIEmbeddings(
    model=EMBEDDING_MODEL,
    api_key=API_KEY,
    base_url=BASE_URL
)


# =====================================================
# CREATE / LOAD FAISS VECTOR DATABASE
# =====================================================

if os.path.exists(VECTOR_DB_PATH):

    print("\nLoading existing FAISS vector database...")

    vector_db = FAISS.load_local(
        VECTOR_DB_PATH,
        embeddings,
        allow_dangerous_deserialization=True
    )

else:

    print("\nCreating FAISS vector database...")

    vector_db = FAISS.from_documents(
        documents,
        embeddings
    )

    vector_db.save_local(VECTOR_DB_PATH)

    print("FAISS vector database created successfully.")


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
# RAG SEARCH QUERY
# =====================================================

search_query = f"""
Treatment / Procedure:
{treatment}

Diagnosis / Reason:
{diagnosis}

Patient Type:
{patient_type}

Find policy information related to:
- Coverage
- Non-covered services
- Exclusions
- Waiting periods
- Prior authorization
- Eligibility
- Treatment requirements
"""


# =====================================================
# RETRIEVE RELEVANT POLICY CHUNKS
# =====================================================

retrieved_docs = vector_db.similarity_search(
    search_query,
    k=5
)

print("\nRelevant policy sections retrieved:")
print(f"Number of sections: {len(retrieved_docs)}")


# =====================================================
# BUILD RAG CONTEXT
# =====================================================

policy_context = ""

for i, doc in enumerate(retrieved_docs):

    policy_context += f"""
==================================================
POLICY SECTION {i + 1}
==================================================

{doc.page_content}

"""


# =====================================================
# FINAL LLM PROMPT
# =====================================================

final_prompt = f"""
You are a healthcare insurance Prior Authorization assistant.

A patient is requesting the following treatment.

Treatment / Procedure:
{treatment}

Diagnosis / Reason:
{diagnosis}

Patient Type:
{patient_type}


IMPORTANT RULES:

1. Use ONLY the insurance policy information provided below.
2. Do not invent coverage.
3. Do not make assumptions.
4. Do not use outside insurance knowledge.
5. If information is not available, write:
   "Not specified in the policy."
6. Clearly identify policy requirements.
7. Do not approve or deny authorization unless the policy
   explicitly provides enough information.


RELEVANT POLICY INFORMATION:

{policy_context}


Generate the following structured response:


PRIOR AUTHORIZATION SUMMARY

Treatment / Procedure:
{treatment}

Diagnosis / Reason:
{diagnosis}

Patient Type:
{patient_type}

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


7. Required Documentation
- ...


8. Decision Notes
- ...


9. Policy Evidence
- Mention the relevant policy sections that support
  the response.
"""


# =====================================================
# FINAL LLM RESPONSE
# =====================================================

final_response = llm.invoke(final_prompt)


# =====================================================
# DISPLAY RESULT
# =====================================================

print("\n")
print("=" * 60)
print("FINAL PRIOR AUTHORIZATION SUMMARY")
print("=" * 60)

print(final_response.content)
