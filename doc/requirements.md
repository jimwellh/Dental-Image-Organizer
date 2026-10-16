# Dental-Image-Organizer — Product Requirements

> English rendition of the original product requirements document (written in Traditional Chinese).
> The original document is kept out of this public repository because its example figures are
> clinical intraoral photographs. Figures are described in text below instead.

## 1. Project Name

**Dental-Image-Organizer** — an automated alignment, organization, and collage tool for clinical
dental photographs.

## 2. Requirements

### 2.1 Background and Goals

This project builds an image-processing tool designed for dentists. The concept is inspired by a
dental photography web app (<https://photo.smile-tw.com/>), but it deliberately drops heavyweight
patient-record management. It focuses on two problems in clinical dental photography:

- **Cross-session consistency** of photos taken at different times.
- **Standardized collage / composite output.**

The system serves two core scenarios:

1. **Pre-op / intra-op / post-op photos (treatment tracking and sequence collage).**
   The dentist manually fine-tunes a pre-op photo, which becomes the patient-specific reference
   ("gold standard"). Later intra-op and post-op photos are automatically cropped, color-corrected,
   and angle-aligned to match it. The processed photos can be exported in one click as a
   horizontal, side-by-side treatment record image.

   *Figure (described):* a slide titled "Procedure" showing five photos of the same posterior
   mandibular site placed side by side, left to right in time order — healed ridge, flap raised,
   implant placed, cover screw, sutured — all at identical scale and framing.

2. **Composite (9-photo full-mouth series).**
   Nine intraoral photos taken from different standard views are automatically cropped, leveled,
   and color-unified, then combined into one standard 3 × 3 clinical composite.

   *Figure (described):* a seamless 3 × 3 grid. Top row: upper-right buccal, upper occlusal
   (mirror view), upper-left buccal. Middle row: right occlusion, frontal occlusion, left
   occlusion. Bottom row: lower-right, lower occlusal (mirror view), lower-left.

### 2.2 Four Core Image-Processing Capabilities

For uploaded clinical photos, the system must provide four automated capabilities:

1. **Crop** — Automatically crop each photo to a consistent field of view of the treatment area,
   following either the framing of the reference photo or the standard cell proportions of the
   9-photo composite.
2. **Color** — Automatically correct white balance, exposure, contrast, and color temperature so
   that tooth shade and gingival color stay consistent within a set (removing lighting differences
   between sessions and between angles).
3. **Angles / Alignment** — Recognize teeth and treatment-area features, then automatically
   rotate, translate, mirror-flip, and fine-tune perspective so that the occlusal plane / horizon
   and the camera angle line up.
4. **Consistency** — Whenever photos of the same patient are placed together, whether as a
   cross-session treatment comparison or as a same-session 9-photo composite, they must look
   visually continuous and meet a standardized quality bar.

### 2.3 Core Features

#### Feature 1 — Pre-op / Intra-op / Post-op Photos (single-site tracking and sequence image)

- **Reference-based processing.** When a case is created for a specific treatment site of a
  patient, the dentist first uploads a pre-op photo that has already been manually perfected.
  This photo is the case baseline. Every intra-op and post-op photo uploaded afterward uses this
  baseline as the single target for alignment and color correction.
- **Two output modes:**
  1. **Individual photos** — export each aligned, color-corrected pre-op, intra-op, and post-op
     photo as its own file.
  2. **Procedure sequence image** — place the processed photos (for example 3 to 5) side by side,
     left to right in chronological order, seamlessly in a single image, for presentations and
     case sharing.

#### Feature 2 — Composite (9-photo 3 × 3 collage)

- **Full-mouth 9-photo composite.** For nine standard intraoral views of the same patient, the
  system automatically arranges and merges them into one borderless (or hairline-bordered) 3 × 3
  image:
  - **Top row (maxillary views):** upper right occlusal/buccal, upper anterior occlusal,
    upper left occlusal/buccal.
  - **Middle row (occlusion views):** right occlusion, frontal occlusion, left occlusion.
  - **Bottom row (mandibular views):** lower right occlusal/buccal, lower anterior occlusal,
    lower left occlusal/buccal.
- **Automatic layout and color.** While compositing, the system must automatically crop away
  excess cheek retractors and lip edges, level the occlusal plane of the middle row to the
  horizon, correct the symmetric angles of the upper and lower arches, and unify the color
  temperature and brightness of all nine photos into one visually coherent composite.

### 2.4 Scope

**In scope**

- Patient-specific reference photos. A patient with several treatment sites must be able to have
  a different reference photo per site.
- Automated alignment and color processing of intra-op and post-op photos of a local treatment
  site.
- Horizontal sequence image of pre-op / intra-op / post-op photos.
- Automatic layout and compositing of the 9-photo 3 × 3 composite.
- Batch export with automatic, standardized file naming.

**Out of scope**

- **Facial and radiographic images.** No smile photos, facial portraits (frontal / profile), or
  radiographs (panoramic / CT). Only intraoral close-ups are laid out.
- **Electronic medical records (EMR).** No clinical notes, insurance claims, or integration with
  hospital / clinic systems. The tool only processes, composes, and exports clinical photos.

### 2.5 Target Platform

Tentatively an Apple macOS desktop application **or** a web application. The platform is not
fixed; the guiding principles are intuitive operation, easy import of photos from phone or
computer, and batch download.

### 2.6 Known Constraints and Variability to Overcome

- **Large variability between patients.** Mouth opening and how far the cheek retractors can be
  pulled differ between patients. Photo quality varies widely, especially in **buccal views**:
  some patients give a clear field of view, while others are partly obscured by lips, retractors,
  or saliva.
- **Consistency within a patient.** A single rigid standard cannot fit every patient. However, the
  pre-op, intra-op, and post-op photos of one patient, or the nine photos of one composite
  session, share the same oral conditions. The system must overcome angle drift and local
  occlusion at capture time and keep that patient's photos highly consistent.

## 3. Workflow Demonstration (User SOP)

### 3.1 Workflow A — Feature 1: Pre-op / Intra-op / Post-op Photos

1. **Pre-op capture.** Before treatment, the dentist photographs the treatment site with their own
   phone.
2. **Create the reference (manual fine-tuning).** Using the phone's built-in photo editor, the
   dentist rotates, crops, and color-grades this pre-op photo until it meets their aesthetic and
   clinical-documentation standard. This becomes the "reference photo."
3. **Create the case and bind the reference.** The dentist opens the app, enters the patient name
   and a dentist-defined procedure / site name, and uploads the reference photo. The system locks
   it as the reference for all later photos of this patient and site.
4. **Automatic intra-op / post-op processing (no manual retouching).** During or after treatment,
   the dentist simply photographs the site (possibly several intra-op steps) and uploads the
   originals into the matching case. Using the reference from step 3, the system automatically
   performs crop, color, and angle / alignment so that every photo stays consistent with the
   reference, with no per-photo manual editing.
5. **Generate the procedure sequence image.** The dentist selects the pre-op, intra-op, and
   post-op photos to show (for example five photos from different stages) and clicks
   "Generate sequence." The system places the selected photos horizontally and tightly, at equal
   height and consistent treatment-area scale, producing one complete treatment comparison image.
6. **Batch download and automatic naming.** When treatment ends or a report is needed, the dentist
   can either enter a patient name or custom tag (such as the procedure name or site) in the
   system, or let the UI organize it directly. One click downloads all processed individual photos
   and sequence images for that patient. Downloaded files are renamed automatically:
   - **Individual photo:** `[Patient]_[Procedure or site]_[Original capture time]`
     — e.g. `AlexWang_implant maxillary right first molar_20261016_1430.jpg`
   - **Sequence image:** `[Patient]_[Procedure or site]_Procedure_[Generation date]`
     — e.g. `AlexWang_implant maxillary right first molar_Procedure_20261016.jpg`

### 3.2 Workflow B — Feature 2: Composite (9-photo collage)

1. **Capture nine intraoral photos.** The dentist photographs the nine standard views with a phone
   (upper right / center / left, occlusion right / front / left, lower right / center / left).
2. **Batch upload to the Composite module.** The dentist opens the Composite module, enters the
   patient name and a capture stage / custom tag, and selects all nine original photos at once.
3. **Automatic slot placement and image normalization.**
   - The system recognizes which 3 × 3 slot each photo belongs to (or the dentist assigns slots
     quickly by drag and drop).
   - For each of the nine photos, the system automatically performs:
     - **Leveling and flipping:** levels the occlusal plane of the frontal and lateral occlusion
       photos, and converts upper / lower occlusal photos taken through an intraoral mirror to the
       correct anatomical orientation.
     - **Smart crop:** focuses on the dental arch and gingiva, cropping away extra-oral background
       and excess retractor edges.
     - **Color matching:** uses the frontal occlusion photo (or a chosen photo) as the color
       baseline and unifies color temperature and brightness across all nine photos.
4. **Preview, fine-tune, and export.** The screen shows a live 3 × 3 preview (the nine photos
   tightly and seamlessly joined). If a cell needs adjustment, the dentist can drag inside it to
   pan or zoom. After confirming, the dentist clicks "Export Composite"; the system outputs one
   high-resolution composite, named automatically:
   - `[Patient]_[Custom tag]_Composite_[Capture date]`
     — e.g. `AlexWang_Comprehensive oral evaluation and full mouth records_Composite_20261016.jpg`
