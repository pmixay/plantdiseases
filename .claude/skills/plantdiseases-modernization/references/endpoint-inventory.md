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

## Table A — Endpoints (0 remaining — Retrofit removed, 26 done)

| Endpoint | Function | Req → Resp | Status |
|---|---|---|---|
| POST /api/analyze (multipart) | `analyzeImage` (in ScanRepository) | image → AnalysisResponse | ✅ **Ktor** |
| GET /api/capabilities | `fetchCapabilities` (in ScanRepository) | — → CapabilitiesResponse | ✅ **Ktor** |
| GET /api/aqua/manifest | `syncAquaModel` (in ScanRepository) | — → AquaManifestResponse | ✅ **Ktor** |
| GET /api/aqua/model (@Streaming) | `syncAquaModel` (in ScanRepository) | — → bytes (streamed) | ✅ **Ktor** |
| GET /api/health | `checkServerHealth` (in ScanRepository) | — → HealthResponse | ✅ **Ktor** |
| POST /api/chat | `sendChat` (in ScanRepository) | ChatRequest → ChatResponse | ✅ **Ktor** |
| POST /api/identify (multipart) | `identifySpecies` (in ScanRepository) | image → IdentifyResponse | ✅ **Ktor** |
| POST /api/care-plan | `carePlan` (PlantApiClient + repo fetchCarePlan) | CarePlanRequest → CarePlanResponse | ✅ **Ktor** |
| POST /api/symptom-diagnose | `diagnoseSymptoms` (in ScanRepository) | SymptomDiagnoseRequest → SymptomDiagnoseResponse | ✅ **Ktor** |
| POST /api/progression | `fetchProgression` (in ScanRepository) | ProgressionRequest → ProgressionResponse | ✅ **Ktor** |
| POST /api/feedback | `submitFeedback` (in ScanRepository) | FeedbackRequest → FeedbackResponse | ✅ **Ktor** |
| POST /api/feedback/image (multipart) | `uploadFeedbackImage` (in ScanRepository) | image + scanId → Unit | ✅ **Ktor** |
| POST /api/community/scan (multipart) | `uploadCommunityScan` (in ScanRepository) | image? + fields → CommunityScanUploadResponse | ✅ **Ktor** |
| GET /api/community/nearby | `communityNearby` (in ScanRepository) | — → CommunityScansResponse | ✅ **Ktor** |
| DELETE /api/community/user/{id} | `deleteCommunityContributions` (in ScanRepository) | — → 2xx | ✅ **Ktor** |
| POST /api/community/opinion | `submitCommunityOpinion` (in ScanRepository) | CommunityOpinionRequest → CommunityOpinionsResponse | ✅ **Ktor** |
| POST /api/community/request-opinion | `requestSecondOpinion` (in ScanRepository) | CommunityRequestOpinionRequest → CommunityRequestOpinionResponse | ✅ **Ktor** |
| GET /api/community/opinions/{scan_id} | `getCommunityOpinions` (in ScanRepository) | — → CommunityOpinionsResponse | ✅ **Ktor** |
| GET /api/community/my-opinions/{id} | `getMyCommunityOpinions` (in ScanRepository) | — → CommunityOpinionsResponse | ✅ **Ktor** |
| GET /api/community/my-scan-opinions/{id} | `fetchMyScanOpinions` (in ScanRepository) | — → MyScanOpinionsResponse | ✅ **Ktor** |
| POST /api/push/register | `registerPushToken` (in ScanRepository) | PushRegisterRequest → 2xx | ✅ **Ktor** |
| POST /api/community/view | `recordCommunityView` (in ScanRepository) | CommunityViewRequest → 2xx | ✅ **Ktor** |
| POST /api/community/scan-counts | `getCommunityScanCounts` (in ScanRepository) | CommunityScanCountsRequest → CommunityScanCountsResponse | ✅ **Ktor** |
| GET /api/community/trending | `communityTrending` (PlantApiClient) | — → TrendingResponse | ✅ **Ktor** |
| GET /api/species-info | `speciesInfo` (PlantApiClient + repo getSpeciesInfo) | — → SpeciesInfoResponse | ✅ **Ktor** |
| GET /api/species-diseases | `speciesDiseases` (PlantApiClient) | — → SpeciesDiseasesResponse | ✅ **Ktor** |
| GET /api/community/by-species | `communityScansBySpecies` (in ScanRepository) | — → CommunityScansResponse | ✅ **Ktor** |

⚠ = needs extra care (multipart / streaming / path param / custom header). Suggested order:
plain GET/POST first (capabilities, health, care-plan, chat, symptom, progression, feedback,
trending, species-*), then path-param GETs, then the multipart/streaming/header ones, label-* last.

## Table B — Models (all transport models migrated; Retrofit + converter-gson removed)

**✅ `@Serializable` already (Models.kt):** DetectionRegion, Detection, TopKEntry,
PredictionSetItem, PredictionSetCareCard, PredictionSet, AnalysisResponse, SpeciesBlock,
SpeciesMatch, CapabilitiesResponse, FamilyCapability, HealthResponse, SymptomDiagnoseRequest,
SymptomResult, SymptomDiagnoseResponse, FeedbackRequest, TopProb, FeedbackResponse,
TrendingResponse, TrendingSpecies, TrendingDisease, SpeciesInfoResponse,
SpeciesDiseasesResponse, SpeciesDiseaseItem, CarePlanRequest, CarePlanResponse (watering_days
via WateringDaysSerializer), LuxBand, HumidityBand, TemperatureBand, CarePlanCheckpoint,
ChatRequest (diagnosis via AnyMapSerializer), ChatResponse, ChatUsage, ChatSession,
ProgressionRequest (history via AnyMapListSerializer), ProgressionResponse, IdentifyResponse,
SpeciesCandidate, CommunityScanUploadResponse,
AquaManifestResponse (classes via GsonJsonObjectSerializer), MyScanOpinionsResponse,
ScanOpinionItem, CommunityScansResponse, CommunityScanItem, CommunityRegionSummary,
CommunityTopDisease, CommunityTopRegion, CommunityOpinion, CommunityOpinionsResponse, CommunityOpinionRequest,
CommunityRequestOpinionRequest, CommunityRequestOpinionResponse, PushRegisterRequest,
CommunityViewRequest, CommunityScanCountsRequest, CommunityScanCount, CommunityScanCountsResponse.

**⬜ Still Gson-only — migrate when their endpoint moves (Models.kt):**
CommunityOpinionRequest, CommunityRequestOpinionRequest, CommunityRequestOpinionResponse,
GuideItem,
GuideCategory (enum), ScanHistoryItem.

**Note:** `LabelingModels.kt` and the `api/label/*` endpoints were removed upstream
(commit 16da03c) — they are not part of this migration.


## Done = step 2 complete
- [ ] All 31 endpoints call Ktor; `ApiService` interface deleted.
- [ ] All 73 models `@Serializable`; `@SerializedName` + Gson + `converter-gson` removed.
- [ ] `JsonObject`/`Map` special cases handled (AquaManifestResponse, AnalysisResponse.allProbs).
- [ ] Every migrated endpoint has a MockWebServer test; `testDebugUnitTest` + `assembleRelease` green.
