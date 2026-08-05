# Third-party notices

First-party source code in this repository is dedicated to the public domain
under the **Unlicense** (see `LICENSE`). The components listed below are **not**
first-party: they retain their own licenses, which govern their use,
redistribution, and the terms below take precedence over `LICENSE` for those
components.

This file is the attribution inventory required by several of those licenses
(Apache-2.0 §4(d), BSD, etc.). It is maintained in English only as a legal
notice; it is not user-facing product content.

> **Scope note.** The runtime server (`server/`) serves inference from
> **exported ONNX graphs via ONNX Runtime**. Training-time tools such as
> Ultralytics YOLOv8 are **not** runtime dependencies of the shipped server or
> app — they are listed here for provenance of the model weights.

## Pre-trained model weights

| Component | Role | License | Upstream |
|---|---|---|---|
| **DINOv2 ViT-S/14** (Meta AI) | Frozen Stage-2 classifier backbone (`gamma`/`frontier`) | Apache-2.0 | https://github.com/facebookresearch/dinov2 |
| **Ultralytics YOLOv8n** | Stage-1 leaf detector; trained then exported to ONNX | AGPL-3.0 | https://github.com/ultralytics/ultralytics |
| MobileNetV3-Small (`aqua` student) | On-device TFLite classifier, knowledge-distilled from `gamma` | torchvision weights: BSD-3-Clause (architecture); distilled weights are first-party | https://github.com/pytorch/vision |

The `aqua` student and the `gamma`/`frontier` MLP heads are trained in-house
(`server/train_notebook.ipynb`); the resulting weights are first-party. The
DINOv2 backbone is used frozen, so its Apache-2.0 terms apply to the bundled
backbone weights.

## Training / evaluation datasets

Used to train and evaluate the models. Not redistributed in this repository;
listed for provenance and to honour each dataset's terms.

| Dataset | Use | Terms |
|---|---|---|
| PlantVillage | Disease classes (lab/studio leaves) | Research use; see dataset source |
| PlantDoc | In-the-wild disease leaves | CC BY 4.0 |
| iNaturalist | Houseplant species + negatives | Per-observation CC licenses (CC BY / CC BY-NC / CC0) |
| COCO | `not_a_plant` negatives | Images: Flickr terms; annotations: CC BY 4.0 |
| Open Images | `not_a_plant` negatives | Annotations: CC BY 4.0; images: various CC |

## Server runtime dependencies (`server/requirements.txt`)

| Package | License |
|---|---|
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| Pillow | HPND (MIT-CMU) |
| ImageHash | BSD-2-Clause |
| NumPy | BSD-3-Clause |
| ONNX Runtime | MIT |
| Requests | Apache-2.0 |
| firebase-admin | Apache-2.0 |

## Android app dependencies (`android/app/build.gradle.kts`)

| Library | License |
|---|---|
| AndroidX (core, appcompat, activity, fragment, constraintlayout, recyclerview, swiperefreshlayout, navigation, camera/CameraX, room, lifecycle, viewpager2, datastore, exifinterface, work) | Apache-2.0 |
| Google Material Components | Apache-2.0 |
| OkHttp + logging-interceptor | Apache-2.0 |
| Gson (Room field + response-cache JSON) | Apache-2.0 |
| Ktor (client-core, okhttp, content-negotiation, serialization) | Apache-2.0 |
| kotlinx.serialization, kotlinx.coroutines | Apache-2.0 |
| Koin | Apache-2.0 |
| Glide | BSD/MIT/Apache-2.0 (mixed; see Glide LICENSE) |
| Lottie | Apache-2.0 |
| Facebook Shimmer | BSD (Facebook) |
| android-image-cropper (vanniktech) | Apache-2.0 |
| Firebase Cloud Messaging | Google APIs Terms of Service |
| Google AI Edge LiteRT / TensorFlow Lite | Apache-2.0 |

## Development / training tooling (not shipped)

| Tool | License |
|---|---|
| PyTorch / torchvision | BSD-3-Clause |
| Ultralytics | AGPL-3.0 (training/export only) |
| scikit-learn | BSD-3-Clause |
| pytest, ruff | MIT |

License versions and texts are available at each upstream link. Where a range
of CC licenses applies (iNaturalist, Open Images images), the most restrictive
per-item license governs that item.
