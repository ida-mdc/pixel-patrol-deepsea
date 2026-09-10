#!/usr/bin/env nextflow

/*
 * Collect expedition video into one parquet per expedition.
 *
 * Five stages, and the interesting ones are the second and the third:
 *
 *   LIST      one HTTP listing per expedition -> a manifest of video URLs
 *   SELECT    that manifest -> the recordings worth analysing. A deep dive spends
 *             hours descending through open water and publishes every minute of
 *             it, so "the first N files" is the one sampling rule guaranteed to
 *             buy footage of nothing. Each dive's own report says how deep it went
 *             and when the vehicle was on the bottom, and reading it costs one
 *             ranged read of a text file per dive - see `selection`.
 *   ANALYSE   one recording -> one parquet. The only stage that touches video,
 *             and it keeps none: the recording is staged into a temporary
 *             directory and deleted before the task ends.
 *   MERGE     the parquets of one expedition -> that expedition's report
 *   SITE      every report -> a static viewer and the page that indexes them
 *
 * Only new work is done, by two mechanisms that catch different things.
 * `-resume` skips any ANALYSE whose inputs have not changed, so re-running after
 * adding an expedition costs nothing for the others. And ANALYSE is skipped
 * outright for a recording whose parquet is already published, so the manifest
 * growing by three dives means three tasks - even on a fresh work directory.
 *
 *   nextflow run main.nf --outdir /data/footage -resume
 *   nextflow run main.nf --outdir /data/footage --expeditions EX2107,EX1903L2
 *   nextflow run main.nf --outdir /data/footage --dives 3 --perDive 3   # a night
 *   nextflow run main.nf --outdir /data/footage -profile slurm
 */

nextflow.enable.dsl = 2

params.outdir      = "collection"
params.expeditions = null      // comma-separated ids; default is the whole catalogue
params.fps         = 10        // thin to this rate before analysing; 0 keeps the original
params.sliceFrames = 10        // 1 s at 10 fps

// What to analyse of each expedition. All three are caps, not quotas: an
// expedition with fewer dives than `dives` gives what it has. The default is
// everything, which for the whole catalogue is about ten thousand recordings -
// set them before starting a cluster run unless that is genuinely the intent.
//
// These are the same choices `collect run` makes for one machine, so a night on a
// workstation and a day on a cluster analyse the same footage.
params.dives       = 0         // deepest dives per expedition, 0 for all
params.perDive     = 0         // recordings per dive, spread over its bottom time
params.limit       = 0         // cap on recordings per expedition, 0 for all

// A cap on what is *chosen*, not on what each run submits: a second run with the
// same limit finds its work already published and does nothing, rather than
// analysing another `limit` recordings.

params.detector       = "general"   // 499 classes; "fish" is one class and calls sponges fish
params.detectorFrames = 1   // crops per animal; 3 is 3x the inference of 1
params.detectEvery    = 1   // seconds between looks; independent of slice length
// Input sizes each frame is read at and fused. Measured on the annotated
// sequences shrunk to NOAA's 640x360 proxy size, a third pass at 1280 bought two
// thousandths of recall for twice the cost - there is no detail in a proxy for a
// larger input to find. So the cruises are read at two sizes; full-resolution
// footage is worth the third.
params.detectorSizes  = "640,960"

// Where a recording is staged while it is analysed. Empty means the task's own
// work directory, which is the only place a workflow can be sure of: it exists,
// every node can see it, and Nextflow cleans it up.
//
// The tempting answer is the node's own /tmp, and on a cluster it is a trap. A
// task asks for one core, so the scheduler packs as many of them onto a node as
// it has cores, and every one of them stages a recording into the same /tmp at
// the same time - 68 MB each for NOAA proxies, 906 MB for an Axial one. Thirty
// tasks on a node with a few gigabytes of /tmp is
//
//     OSError: [Errno 28] No space left on device
//
// out of the fetch, after the download. Point this at node-local scratch if you
// have some that is genuinely large; otherwise leave it alone.
params.staging        = ""

// Every process runs through this, so the workflow does not depend on the caller
// having the right environment active. Override for a different install:
//   --python "conda run -n myenv python"   or   --python /path/to/venv/bin/python
params.condaEnv    = "pixel-patrol"
params.python      = "micromamba run -n ${params.condaEnv} python"

process LIST {
    tag "${expedition}"
    publishDir "${params.outdir}/manifests", mode: 'copy'

    input:
    val expedition

    output:
    tuple val(expedition), path("${expedition}.json")

    script:
    """
    ${params.python} -m pixel_patrol_deepsea.collect list ${expedition} -o ${expedition}.json
    """
}

process SELECT {
    tag "${expedition}"
    publishDir "${params.outdir}/chosen", mode: 'copy'
    // A few ranged reads of the archive's dive reports, no video. Retried because
    // it is the one stage that is nothing but HTTP.
    errorStrategy 'retry'
    maxRetries 1

    input:
    tuple val(expedition), path(manifest)

    output:
    tuple val(expedition), path("${expedition}.chosen.json")

    script:
    def picks = [params.dives   ? "--dives ${params.dives}"      : "",
                 params.perDive ? "--per-dive ${params.perDive}" : "",
                 params.limit   ? "--most ${params.limit}"       : ""].findAll().join(" ")
    """
    ${params.python} -m pixel_patrol_deepsea.collect choose ${expedition} \\
        -m ${manifest} -o ${expedition}.chosen.json ${picks}
    """
}

process ANALYSE {
    tag "${expedition}/${name}"
    // A closure, not a plain string: this path depends on an input value, and
    // Nextflow 26 resolves a directive's string when the process is defined,
    // where `expedition` is not bound to anything yet.
    publishDir path: { "${params.outdir}/parts/${expedition}" }, mode: 'copy'
    // One recording of video and one model in memory. Retried once because an
    // archive serving thousands of hours will drop a connection now and then.
    memory '8 GB'
    errorStrategy 'retry'
    maxRetries 1

    input:
    tuple val(expedition), val(name), val(url)

    output:
    tuple val(expedition), path("${name}.parquet")

    script:
    def thin = params.fps ? "--fps ${params.fps}" : "--fps 0"
    def staging = params.staging ? "export TMPDIR='${params.staging}'" : 'export TMPDIR="$PWD"'
    """
    ${staging}
    ${params.python} -m pixel_patrol_deepsea.collect one '${url}' \\
        -o ${name}.parquet -e ${expedition} ${thin} \\
        --slice-frames ${params.sliceFrames} --detector ${params.detector} \\
        --detector-frames ${params.detectorFrames} \\
        --detector-sizes ${params.detectorSizes} \\
        --detect-every ${params.detectEvery}
    """
}

process MERGE {
    tag "${expedition}"
    publishDir "${params.outdir}/parquet", mode: 'copy'

    input:
    tuple val(expedition), path(parts)

    output:
    path "${expedition}.parquet"

    script:
    """
    ${params.python} -m pixel_patrol_deepsea.collect merge ${expedition} ${parts} \\
        -o ${expedition}.parquet
    """
}

process SITE {
    publishDir "${params.outdir}", mode: 'copy'

    input:
    path reports
    path manifests

    output:
    path "index.html"
    path "viewer", optional: true

    script:
    // The page is built in the task directory, so everything it reads has to be
    // staged here. Without the manifests it has no denominator and every
    // expedition reads "0 of 0" however much has actually been analysed - which is
    // why SITE is given the full listing and not what SELECT picked out of it.
    """
    mkdir -p parquet manifests
    cp ${reports} parquet/
    cp ${manifests} manifests/
    ${params.python} -m pixel_patrol_deepsea.collect site .
    """
}

// Functions rather than closures assigned to names: Nextflow 26's strict syntax
// rejects a top-level `def x = { ... }` as a statement mixed in with the script's
// declarations, and the workflow should run on both.
def nameOf(String url) {
    url.tokenize('/')[-1].replaceAll(/\.[^.]+$/, '')
}

/* Recordings whose parquet is already published need no task at all. This is what
 * makes "check for newly added expedition videos" cheap on a fresh work directory,
 * where -resume has no cache to consult. */
def unprocessed(String expedition, List urls) {
    def parts = file("${params.outdir}/parts/${expedition}")
    def done = parts.exists()
        ? parts.list().findAll { it.endsWith('.parquet') }.collect { it - '.parquet' } as Set
        : [] as Set
    urls.findAll { !done.contains(nameOf(it)) }
}

/* The detector is 200 MB fetched once into a cache, and every ANALYSE needs it.
 * Checked here rather than discovered by a thousand tasks failing one after
 * another on a queue - and checked through `params.python`, so it is the
 * interpreter the tasks will actually use that gets asked. */
def checkDetector() {
    def probe = ["bash", "-lc", "${params.python} -c 'import sys; " +
                "from pixel_patrol_deepsea import detector; " +
                "sys.exit(0 if detector.is_available() else 1)'"].execute()
    probe.waitFor()
    if (probe.exitValue() != 0) {
        error """no detector where ${params.python} can reach it.
        Fetch it once, into a cache the compute nodes share:
            ${params.python} -m pixel_patrol_deepsea.fetch_detector --model general"""
    }
}

workflow {
    checkDetector()

    def wanted = params.expeditions
        ? Channel.fromList(params.expeditions.tokenize(','))
        : Channel.fromPath("${projectDir}/../src/pixel_patrol_deepsea/expeditions.yaml")
                 .splitText()
                 .map { line -> (line =~ /^- id:\s*(\S+)/) ? (line =~ /^- id:\s*(\S+)/)[0][1] : null }
                 .filter { it != null }

    manifests = LIST(wanted)
    chosen    = SELECT(manifests)

    todo = chosen
        .map { expedition, json ->
            def urls = new groovy.json.JsonSlurper().parse(json.toFile()).videos
            def pending = unprocessed(expedition, urls)
            log.info "${expedition}: ${urls.size()} chosen, ${pending.size()} to analyse"
            [expedition, pending]
        }
        .flatMap { expedition, urls -> urls.collect { [expedition, nameOf(it), it] } }

    parts = ANALYSE(todo)

    // Everything published so far, not only what this run made, so a partial
    // collection still merges into a report you can open.
    published = Channel.fromPath("${params.outdir}/parts/*/*.parquet")
                       .map { [it.parent.name, it] }
    MERGE(parts.mix(published).groupTuple())
    SITE(MERGE.out.collect(), manifests.map { _id, json -> json }.collect())
}
