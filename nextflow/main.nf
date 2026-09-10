#!/usr/bin/env nextflow

/*
 * Collect expedition video into one parquet per expedition.
 *
 * Four stages, and the interesting one is the second:
 *
 *   LIST      one HTTP listing per expedition -> a manifest of video URLs
 *   ANALYSE   one recording -> one parquet. The only stage that touches video,
 *             and it keeps none: the recording is staged into the task's own
 *             work directory and goes away with it.
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
 *   nextflow run main.nf --outdir /data/footage --limit 20     # a taste of each
 */

nextflow.enable.dsl = 2

params.outdir      = "collection"
params.expeditions = null      // comma-separated ids; default is the whole catalogue
params.limit       = 0         // recordings per expedition, 0 for all
params.fps         = 10        // thin to this rate before analysing; 0 keeps the original
params.sliceFrames = 50
params.detector    = "general"   // 499 classes; "fish" is one class and calls sponges fish
params.detectorFrames = 3   // crops per animal; 3x the inference of 1
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

process ANALYSE {
    tag "${expedition}/${name}"
    publishDir "${params.outdir}/parts/${expedition}", mode: 'copy'
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
    """
    ${params.python} -m pixel_patrol_deepsea.collect one '${url}' \\
        -o ${name}.parquet -e ${expedition} ${thin} \\
        --slice-frames ${params.sliceFrames} --detector ${params.detector} \\
        --detector-frames ${params.detectorFrames}
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
    // expedition reads "0 of 0" however much has actually been analysed.
    """
    mkdir -p parquet manifests
    cp ${reports} parquet/
    cp ${manifests} manifests/
    ${params.python} -m pixel_patrol_deepsea.collect site .
    """
}

def nameOf = { url -> url.tokenize('/')[-1].replaceAll(/\.[^.]+$/, '') }

/* Recordings whose parquet is already published need no task at all. This is what
 * makes "check for newly added expedition videos" cheap on a fresh work directory,
 * where -resume has no cache to consult. */
def unprocessed = { expedition, urls ->
    def done = file("${params.outdir}/parts/${expedition}").exists()
        ? file("${params.outdir}/parts/${expedition}").list().findAll { it.endsWith('.parquet') }
                                                            .collect { it - '.parquet' } as Set
        : [] as Set
    urls.findAll { !done.contains(nameOf(it)) }
}

workflow {
    def wanted = params.expeditions
        ? Channel.fromList(params.expeditions.tokenize(','))
        : Channel.fromPath("${projectDir}/../src/pixel_patrol_deepsea/expeditions.yaml")
                 .splitText()
                 .map { line -> (line =~ /^- id:\s*(\S+)/) ? (line =~ /^- id:\s*(\S+)/)[0][1] : null }
                 .filter { it != null }

    manifests = LIST(wanted)

    todo = manifests
        .map { expedition, json ->
            def urls = new groovy.json.JsonSlurper().parse(json.toFile()).videos
            def pending = unprocessed(expedition, urls)
            if (params.limit > 0) pending = pending.take(params.limit as int)
            log.info "${expedition}: ${urls.size()} listed, ${pending.size()} to analyse"
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
