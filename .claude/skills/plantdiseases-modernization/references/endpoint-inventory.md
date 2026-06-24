# Endpoint & model inventory (Ktor + kotlinx.serialization migration)

The exhaustive checklist for finishing step 2. Tick items as you migrate them. Verify against
code before trusting status — this is a point-in-time snapshot.

**Where things live:** `ApiService.kt` (Retrofit interface) and `PlantApiClient` (both transports,
at the **end of `ApiService.kt`, ≈ lines 377–425**) in `data/remote/`. Models in
`data/model/Models.kt` + `LabelingModels.kt`. The already-migrated `/api/analyze` lives in
`ScanRepository.analyzeImage` using `apiClient.httpClient`.

## How to migrate ONE endpoint (repeat per row in Table A)
1. Make its **request + response models** `@Serializable` (Table B). Add `@SerialName` next to
   each existing `@SerializedName`; **keep both** (dual-annotated) until Retrofit is fully gone.
   Give optionals defaults; rely on the shared `Json { ignoreUnknownKeys = true; coerceInputValues
   = true; explicitNulls = false }`.
2. Add a Ktor call (mirror `ScanRepository.analyzeImage`): `apiClient.httpClient.get/post("api/…")`
   with `parameter(...)` for `@Query`, path interpolation for `@Path`, `header(...)` for `@Header`,
   `setBody(...)` (+ `MultiPartFormDataContent` for `@Multipart`, `bodyAsChannel()` for `@Streaming`).
3. Reproduce the **status → typed-exception mapping** (`expectSuccess = false`, so check
   `response.status` yourself — same codes the Retrofit path mapped).
4. Move the call off `ApiService` (remove the interface method once nothing calls it) and update
   its caller/repository.
5. Add/port a test ([testing.md](testing.md)) — success + the relevant error codes. Remember:
   Ktor `ContentNegotiation` only decodes responses with `Content-Type: application/json`, so
   test success mocks must set that header (the real server does).

> **Note:** `Map<String, Float>` (`AnalysisResponse.allProbs`) and `com.google.gson.JsonObject`
> (`AquaManifestResponse.classes`) need attention: maps serialize fine with kotlinx, but the raw
> Gson `JsonObject` field must become `JsonObject` from `kotlinx.serialization.json` (or a typed
> model). Flag these two when you reach them.

## Table A — Endpoints (27 remaining on Retrofit, 5 done)

| Endpoint | Function | Req → Resp | Status |
|---|---|---|---|
| POST /api/analyze (multipart) | `analyzeImage` (in ScanRepository) | image → AnalysisResponse | ✅ **Ktor** |
| GET /api/capabilities | `fetchCapabilities` (in ScanRepository) | — → CapabilitiesResponse | ✅ **Ktor** |
| GET /api/aqua/manifest | `aquaManifest` | — → AquaManifestResponse | ⬜ Retrofit |
| GET /api/aqua/model (@Streaming) | `aquaModel` | — → ResponseBody (bytes) | ⬜ Retrofit ⚠ streaming |
| GET /api/health | `checkServerHealth` (in ScanRepository) | — → HealthResponse | ✅ **Ktor** |
| POST /api/chat | `chat` | ChatRequest → ChatResponse | ⬜ Retrofit |
| POST /api/identify (multipart) | `identifySpecies` | image → IdentifyResponse | ⬜ Retrofit ⚠ multipart |
| POST /api/care-plan | `carePlan` | CarePlanRequest → CarePlanResponse | ⬜ Retrofit |
| POST /api/symptom-diagnose | `diagnoseSymptoms` (in ScanRepository) | SymptomDiagnoseRequest → SymptomDiagnoseResponse | ✅ **Ktor** |
| POST /api/progression | `progression` | ProgressionRequest → ProgressionResponse | ⬜ Retrofit |
| POST /api/feedback | `submitFeedback` (in ScanRepository) | FeedbackRequest → FeedbackResponse | ✅ **Ktor** |
| POST /api/feedback/image (multipart) | `feedbackImage` | image + scanId → ResponseBody | ⬜ Retrofit ⚠ multipart |
| POST /api/community/scan (multipart) | `communityScanUpload` | image? + fields → CommunityScanUploadResponse | ⬜ Retrofit ⚠ multipart |
| GET /api/community/nearby | `communityScanNearby` | — → CommunityScansResponse | ⬜ Retrofit |
| DELETE /api/community/user/{id} | `communityScanDeleteUser` | — → FeedbackResponse | ⬜ Retrofit ⚠ path |
| POST /api/community/opinion | `communitySubmitOpinion` | CommunityOpinionRequest → CommunityOpinionsResponse | ⬜ Retrofit |
| POST /api/community/request-opinion | `communityRequestOpinion` | CommunityRequestOpinionRequest → CommunityRequestOpinionResponse | ⬜ Retrofit |
| GET /api/community/opinions/{scan_id} | `communityGetOpinions` | — → CommunityOpinionsResponse | ⬜ Retrofit ⚠ path |
| GET /api/community/my-opinions/{id} | `communityMyOpinions` | — → CommunityOpinionsResponse | ⬜ Retrofit ⚠ path |
| GET /api/community/my-scan-opinions/{id} | `communityMyScanOpinions` | — → MyScanOpinionsResponse | ⬜ Retrofit ⚠ path |
| POST /api/push/register | `registerPushToken` | PushRegisterRequest → ResponseBody | ⬜ Retrofit |
| POST /api/community/view | `communityRecordView` | CommunityViewRequest → ResponseBody | ⬜ Retrofit |
| POST /api/community/scan-counts | `communityScanCounts` | CommunityScanCountsRequest → CommunityScanCountsResponse | ⬜ Retrofit |
| GET /api/community/trending | `communityTrending` | — → TrendingResponse | ⬜ Retrofit |
| GET /api/species-info | `speciesInfo` | — → SpeciesInfoResponse | ⬜ Retrofit |
| GET /api/species-diseases | `speciesDiseases` | — → SpeciesDiseasesResponse | ⬜ Retrofit |
| GET /api/community/by-species | `communityBySpecies` | — → CommunityScansResponse | ⬜ Retrofit |
| POST /api/label/login | `labelLogin` | LabelLoginRequest → LabelLoginResponse | ⬜ Retrofit |
| GET /api/label/next | `labelNext` | — (X-Label-Token) → LabelNextResponse | ⬜ Retrofit ⚠ header |
| POST /api/label/verdict | `labelVerdict` | LabelVerdictBatch → LabelVerdictResponse | ⬜ Retrofit |
| GET /api/label/stats | `labelStats` | — (X-Label-Token) → LabelStatsResponse | ⬜ Retrofit ⚠ header |
| GET /api/label/clue/{klass} | `labelClue` | — (X-Label-Token) → LabelClueResponse | ⬜ Retrofit ⚠ path+header |
| POST /api/label/export | `labelExport` | — (X-Label-Token) → LabelExportResponse | ⬜ Retrofit ⚠ header |

⚠ = needs extra care (multipart / streaming / path param / custom header). Suggested order:
plain GET/POST first (capabilities, health, care-plan, chat, symptom, progression, feedback,
trending, species-*), then path-param GETs, then the multipart/streaming/header ones, label-* last.

## Table B — Models (18 of 73 migrated)

**✅ `@Serializable` already (Models.kt):** DetectionRegion, Detection, TopKEntry,
PredictionSetItem, PredictionSetCareCard, PredictionSet, AnalysisResponse, SpeciesBlock,
SpeciesMatch, CapabilitiesResponse, FamilyCapability, HealthResponse, SymptomDiagnoseRequest,
SymptomResult, SymptomDiagnoseResponse, FeedbackRequest, TopProb, FeedbackResponse.

**⬜ Still Gson-only — migrate when their endpoint moves (Models.kt):**
MyScanOpinionsResponse, ScanOpinionItem,
AquaManifestResponse ⚠(JsonObject field), CommunityScansResponse, CommunityScanItem,
CommunityRegionSummary, CommunityTopDisease, CommunityTopRegion, CommunityScanUploadResponse,
CommunityOpinionRequest, CommunityRequestOpinionRequest, CommunityRequestOpinionResponse,
CommunityOpinion, CommunityOpinionsResponse, PushRegisterRequest, CommunityViewRequest,
CommunityScanCountsRequest, CommunityScanCount, CommunityScanCountsResponse, GuideItem,
GuideCategory (enum), TrendingResponse, TrendingSpecies, TrendingDisease, ScanHistoryItem,
ChatRequest, ChatResponse, ChatUsage, ChatSession, SpeciesCandidate, IdentifyResponse,
SpeciesInfoResponse, SpeciesDiseasesResponse, SpeciesDiseaseItem, CarePlanRequest,
CarePlanResponse, LuxBand,
HumidityBand, TemperatureBand, CarePlanCheckpoint, ProgressionRequest,
ProgressionResponse.

**⬜ Still Gson-only (LabelingModels.kt):** LabelLoginRequest, LabelLoginResponse, LabelCard,
LabelNextResponse, LabelVerdictItem, LabelVerdictBatch, LabelVerdictResponse, LabelClassStat,
LabelSourceStat, LabelStatsResponse, LabelClueItem, LabelClueResponse, LabelExportResponse.

> Some `⬜` models are nested inside a migrated parent's graph but not yet annotated — when you
> annotate a response, annotate every type it transitively references, or kotlinx won't compile
> a serializer for it.

## Done = step 2 complete
- [ ] All 31 endpoints call Ktor; `ApiService` interface deleted.
- [ ] All 73 models `@Serializable`; `@SerializedName` + Gson + `converter-gson` removed.
- [ ] `JsonObject`/`Map` special cases handled (AquaManifestResponse, AnalysisResponse.allProbs).
- [ ] Every migrated endpoint has a MockWebServer test; `testDebugUnitTest` + `assembleRelease` green.
