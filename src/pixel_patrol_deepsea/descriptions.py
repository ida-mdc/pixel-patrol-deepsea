"""What a branch of the taxonomy is, in a sentence, for a reader who is not a
taxonomist.

The collection page can already say how many animals are under a name and link to
the World Register of Marine Species for the formal record. Neither answers the
question somebody clicking `Holothuroidea` actually has, which is *what am I
looking at*. WoRMS is a nomenclatural register - it holds ranks, authorities and
synonymies, not descriptions - so there is nothing to fetch: these are written
here, once, and shipped with the package.

Only the branches a reader lands on. Every kingdom, phylum and class that this
footage has turned up, the orders and families that hold enough animals to be
worth a click, and the names this project invented (`Undecided`) or the detector
did (`LRJ complex`). Below that the page composes what it can from the rank and
lineage the register did give - "a genus of Cladorhizidae, in the order
Poecilosclerida" - which is honest and is better than silence.

Descriptions are of the group, deliberately, and not of the animals in these
particular frames. What a sea cucumber is does not depend on what the detector
thought it saw, and a description that hedged every sentence would be unreadable.
The disclaimer above the tree is where the doubt belongs.
"""

from typing import Dict, List, Optional

# name -> one sentence or two. Plain words first, the formal ones second.
NOTES: Dict[str, str] = {
    # ── what the page opens on ────────────────────────────────────────────────
    "everything": (
        "Every animal the detector found, arranged by what it called them. Click a "
        "phylum to go into it; the pictures beside the ring follow whatever is in "
        "focus."),

    # ── kingdoms and the two buckets that are not ─────────────────────────────
    "Animalia": (
        "Animals: multicellular, eat other things, and move at some point in their "
        "lives. Almost everything in this collection."),
    "Chromista": (
        "Single-celled life that is neither animal, plant nor fungus. On this "
        "seabed it means the giant protists - amoebae the size of a grape that "
        "build shells out of what they are sitting on."),
    "Unplaced": (
        "Names the World Register of Marine Species does not carry: the detector's "
        "own labels for animals it names its own way, and the animals whose looks "
        "disagreed too far to be named at all."),
    "Undecided": (
        "One animal the detector called several different things - a fish one "
        "second, a sponge the next - with no rank the guesses shared. Rather than "
        "pick the loudest, the name was dropped. Worth a look: this is where the "
        "detector is least sure, and where something unusual is likeliest to be."),
    "Medusae": (
        "Jellyfish, in the loose sense the detector uses it: a swimming bell with "
        "tentacles, without saying whose. Not a name any register carries, which is "
        "why it sits outside the taxonomy."),
    "Hydromedusae": (
        "The medusa - the free-swimming, bell-shaped stage - of a hydrozoan, which "
        "spends the rest of its life as a stalked colony on the bottom. An informal "
        "name for a life stage rather than a group, so no register carries it."),
    "LRJ complex": (
        "A composite label out of the detector's own vocabulary rather than any "
        "register: a set of animals the model is not asked to tell apart, because "
        "in a frame this size they cannot be. Read it as a refusal to choose, not "
        "as a group of relatives."),

    # ── phyla ─────────────────────────────────────────────────────────────────
    "Cnidaria": (
        "Anemones, corals, sea pens, hydroids and jellyfish. A ring of stinging "
        "tentacles around a single opening, and no front or back. More of this "
        "footage is cnidarian than anything else."),
    "Echinodermata": (
        "Sea stars, sea cucumbers, brittle stars, urchins and their relatives. "
        "Five-part symmetry, a skeleton of chalky plates, and hundreds of "
        "water-powered tube feet instead of muscle and limb."),
    "Chordata": (
        "Animals with a stiffening rod down the back at some stage of life - the "
        "fishes here, and also the sea squirts and the drifting larvaceans, which "
        "look nothing like them and are close relatives all the same."),
    "Porifera": (
        "Sponges: no nerves, no muscle, no gut. A body built as a pumping system "
        "that strains food out of the water going through it. On hard deep bottoms "
        "they are often the tallest thing in frame."),
    "Arthropoda": (
        "Jointed legs and an external skeleton that has to be shed to grow - the "
        "crabs, squat lobsters, shrimp, krill and isopods."),
    "Mollusca": (
        "Snails, clams, octopus and squid. A muscular foot, a rasping tongue, and a "
        "shell that some of them have given up entirely."),
    "Annelida": (
        "Segmented worms. Down here that includes the tubeworms of hydrothermal "
        "vents and the polychaetes whose fans are the only part of them visible."),
    "Ctenophora": (
        "Comb jellies: eight rows of beating hairs that break the ROV's lights into "
        "a running rainbow. They catch prey with sticky cells rather than stings, "
        "and are not jellyfish."),
    "Brachiopoda": (
        "Lamp shells - two shells like a clam's, hinged the other way round, on a "
        "stalk. Abundant for most of the fossil record and rare now, which is why "
        "finding one on camera is a small event."),
    "Chaetognatha": (
        "Arrow worms: transparent midwater hunters a centimetre or two long, with a "
        "ring of grasping spines around the mouth."),
    "Hemichordata": (
        "Acorn worms. The deep-sea ones graze the sediment surface and leave the "
        "spiral trails that show up in seabed photographs."),
    "Foraminifera": (
        "Single-celled amoebae that build a shell, sometimes out of grains they glue "
        "together. The deep-sea xenophyophores grow to the size of a fist, which is "
        "extraordinary for one cell."),

    # ── classes ───────────────────────────────────────────────────────────────
    "Hexacorallia": (
        "Anemones, stony corals, black corals and zoanthids: cnidarian polyps built "
        "in sixes rather than eights."),
    "Octocorallia": (
        "Soft corals, sea fans, sea pens and bamboo corals - polyps with eight "
        "feathery tentacles. The branching, tree-like growths on deep hard bottoms."),
    "Hydrozoa": (
        "Hydroids and their medusae, including the siphonophores: colonies of "
        "specialised bodies living as one animal, some of them tens of metres long."),
    "Scyphozoa": "The true jellyfish - a swimming bell trailing stinging tentacles.",
    "Teleostei": (
        "Almost every bony fish alive, from grenadiers to flatfish. A mobile upper "
        "jaw they can shoot forwards is what the group is built around."),
    "Elasmobranchii": "Sharks, skates and rays: a skeleton of cartilage, not bone.",
    "Holocephali": (
        "Chimaeras - ratfish. A cartilaginous relative of the sharks with a "
        "rabbit-like face and a whip of a tail."),
    "Myxini": (
        "Hagfish: jawless, eel-shaped scavengers that tie themselves in knots and "
        "smother anything that bothers them in mucus."),
    "Holothuroidea": (
        "Sea cucumbers: leathery sausages that mine the mud for the organic matter "
        "in it. Whole deep-sea plains are grazed by them, and some swim."),
    "Ophiuroidea": (
        "Brittle stars - five whippy arms on a small disc, usually wedged into "
        "something with only the arms showing."),
    "Asteroidea": (
        "Sea stars. Slow, thorough predators that push their stomach out through "
        "their own mouth to eat."),
    "Echinoidea": (
        "Sea urchins and the burrowing heart urchins: a globe or a helmet of plates "
        "carrying spines, scraping the bottom with a five-part jaw."),
    "Crinoidea": (
        "Sea lilies and feather stars: an echinoderm that filters the current with "
        "its arms, some of them still on the stalk their ancestors had."),
    "Malacostraca": (
        "The large crustaceans - crabs, shrimp, squat lobsters, krill, isopods and "
        "amphipods."),
    "Thecostraca": "Barnacles: a crustacean that glued its head down and gave up moving.",
    "Gastropoda": "Snails and slugs, shelled and not, crawling on one muscular foot.",
    "Bivalvia": (
        "Clams, mussels and scallops. At vents and seeps they run on sulphide, "
        "farming bacteria in their own gills."),
    "Cephalopoda": (
        "Octopus, squid and their kin: the most complex nervous system outside the "
        "vertebrates, wrapped in a boneless body that can change colour and texture "
        "at will."),
    "Polychaeta": (
        "Bristle worms. Each segment carries a pair of bristled paddles; most of "
        "what is visible on camera is a tube, a fan or a trail."),
    "Clitellata": "The worms with a saddle - earthworms and leeches.",
    "Demospongiae": (
        "Most sponges: a skeleton of protein and glassy spикules, from crusts to "
        "the metre-wide carnivorous cladorhizids of the deep."),
    "Hexactinellida": (
        "Glass sponges. A skeleton of six-rayed silica spicules, sometimes fused "
        "into a rigid lattice that outlives the animal and becomes reef."),
    "Appendicularia": (
        "Larvaceans: tadpole-shaped animals that build and discard a mucus house "
        "many times their own size. Those discarded houses carry a large share of "
        "the carbon that sinks to the deep sea."),
    "Thaliacea": (
        "Salps and pyrosomes - drifting colonies of filter feeders, pumping "
        "themselves along by squeezing water through the body."),
    "Ascidiacea": (
        "Sea squirts: a chordate that settles head-down as a larva and spends its "
        "life as a sac with two openings."),
    "Rhynchonellata": "The lamp shells that still have a stalk and a hinge with teeth.",
    "Sagittoidea": "The arrow worms - all of them.",
    "Tentaculata": (
        "Comb jellies that fish with two long sticky tentacles trailed behind the "
        "body."),
    "Nuda": (
        "Comb jellies with no tentacles at all: a swimming mouth that engulfs other "
        "comb jellies whole."),
    "Monothalamea": (
        "Single-chambered foraminifera, including the xenophyophores - giant "
        "single-celled animals that build a fist-sized body from cemented sediment."),
    "Globothalamea": "Foraminifera whose shells are built as a spiral of chambers.",
    "Enteropneusta": "The acorn worms themselves.",
    "Aves": (
        "Birds. In footage of the deep sea this is the surface, at the start or the "
        "end of a dive - an albatross over the ship."),

    # ── orders worth a click ──────────────────────────────────────────────────
    "Actiniaria": (
        "Sea anemones: a single polyp, stuck down or burrowed in, with no skeleton "
        "of its own."),
    "Zoantharia": (
        "Zoanthids - mat-forming polyps that often live on somebody else's skeleton, "
        "a coral branch or a sponge."),
    "Scleractinia": (
        "Stony corals. The deep-water ones build reef in cold, dark water with no "
        "algae in them at all."),
    "Antipatharia": (
        "Black corals: a horny black skeleton, often the oldest living thing on a "
        "seamount - individual colonies have been dated at thousands of years."),
    "Corallimorpharia": "Anemone-like polyps with a coral's anatomy and no skeleton.",
    "Scleralcyonacea": (
        "The octocorals with a hard skeleton - bamboo corals, precious corals, sea "
        "pens and the umbrella-like Umbellula."),
    "Malacalcyonacea": "The soft octocorals: fleshy sea fans and soft corals.",
    "Pennatulacea": (
        "Sea pens: a colony shaped like a quill, rooted in mud, feeding on whatever "
        "the current brings."),
    "Semaeostomeae": (
        "The big flag-mouthed jellyfish - the moon jelly, the deep-sea Poralia and "
        "the giant Stygiomedusa."),
    "Coronatae": "Crown jellyfish: a bell with a groove around it, mostly deep water.",
    "Trachymedusae": (
        "Small, tough hydrozoan jellyfish of the open water, Benthocodon among them "
        "- a red bell that hovers just above the seabed."),
    "Narcomedusae": (
        "Hydrozoan jellyfish that swim with their tentacles held up and forwards, "
        "trailing nothing."),
    "Siphonophorae": (
        "Colonies of specialised individuals strung together - swimming bells, "
        "feeding polyps, stinging tentacles - working as one animal. The "
        "Portuguese man o' war is the famous one; most are deep and fragile."),
    "Decapoda": "Ten legs: crabs, shrimp, squat lobsters and their relatives.",
    "Euphausiacea": (
        "Krill. Shrimp-like, swarming, and the link between plankton and almost "
        "everything larger."),
    "Isopoda": (
        "Isopods - woodlouse-shaped crustaceans, flattened top to bottom. The deep "
        "ones include the famous dinner-plate-sized Bathynomus."),
    "Mysida": "Opossum shrimp: small crustaceans that brood their young in a pouch.",
    "Elasipodida": (
        "The swimming and sail-bearing sea cucumbers, including the sea pig - "
        "Elpidiidae - which walks the abyssal mud on inflated tube feet."),
    "Synallactida": "Heavy-bodied deposit-feeding sea cucumbers of the deep bottom.",
    "Dendrochirotida": (
        "Sea cucumbers that feed with a crown of branched tentacles and can pull "
        "them in entirely."),
    "Holasteroida": (
        "Heart urchins - thin-shelled urchins that burrow, including the "
        "bottle-shaped Pourtalesia."),
    "Camarodonta": "The familiar globular sea urchins, spines and all.",
    "Cidaroida": "Pencil urchins: few, thick, blunt spines, often encrusted with life.",
    "Velatida": (
        "Cushion and slime stars - soft, swollen sea stars, most of them deep."),
    "Forcipulatida": (
        "Sea stars with pincers among their spines, the familiar predatory ones "
        "among them."),
    "Valvatida": "Sea stars with a rim of plate-like spines; many are cushion-shaped.",
    "Euryalida": (
        "Basket stars and their relatives: brittle stars with arms that branch and "
        "curl, held up into the current as a net."),
    "Gadiformes": (
        "Cods and grenadiers. The grenadiers - rattails - are among the most "
        "numerous fish on the deep-sea floor anywhere."),
    "Pleuronectiformes": (
        "Flatfish: a fish lying on its side with one eye migrated across the skull."),
    "Perciformes": "A large assortment of spiny-finned fishes, rockfishes among them.",
    "Rajiformes": "Skates - flattened cartilaginous fish that lay eggs in leathery cases.",
    "Lophiiformes": (
        "Anglerfish: a fishing rod grown from the first dorsal spine, lit by "
        "bacteria in the deep-sea ones."),
    "Anguilliformes": "True eels.",
    "Aulopiformes": (
        "Lizardfish and their deep relatives, including the tripod fish that stands "
        "on stiffened fins facing the current."),
    "Chimaeriformes": "Chimaeras: ratfish, ghost sharks.",
    "Myxiniformes": "Hagfish.",
    "Octopoda": (
        "Octopus - eight arms, no shell. The deep-sea ones include the finned "
        "Grimpoteuthis and Opisthoteuthis, which flap rather than swim."),
    "Oegopsida": (
        "Most of the open-ocean squid: hooks as well as suckers, and no cornea over "
        "the eye."),
    "Vampyromorpha": (
        "One species: the vampire squid, which is neither, and lives where there is "
        "almost no oxygen, eating what sinks."),
    "Pteropoda": (
        "Sea butterflies and sea angels - snails that swim with a pair of wings "
        "where the foot was."),
    "Nudibranchia": "Sea slugs: a snail that gave up its shell and got colourful.",
    "Neogastropoda": "Predatory and scavenging sea snails - whelks and their kin.",
    "Venerida": "Clams, the vesicomyids among them - the seep clams that farm bacteria.",
    "Pectinida": "Scallops and their relatives; some of them swim by clapping the shell.",
    "Sabellida": (
        "Fan worms and the vent tubeworms: a worm in a tube, feeding with a crown "
        "that vanishes the instant anything comes close."),
    "Terebellida": "Spaghetti worms - a buried body and long feeding tentacles on the mud.",
    "Phyllodocida": (
        "Active, bristled, often swimming polychaetes, including the deep-sea "
        "Tomopteris."),
    "Poecilosclerida": (
        "A very large order of demosponges, and the one the carnivorous "
        "Cladorhizidae belong to."),
    "Lyssacinosida": "Glass sponges whose spicules stay loose rather than fusing.",
    "Terebratulida": "The lamp shells most often found alive today.",
    "Lobata": "Comb jellies with two large muscular lobes held open around the mouth.",
    "Beroida": "The tentacle-less comb jellies: they swallow other comb jellies.",
    "Platyctenida": (
        "Comb jellies that crawl - flattened, benthic, and easy to mistake for a "
        "flatworm."),
    "Copelata": "The larvaceans.",
    "Pyrosomatida": (
        "Pyrosomes: a hollow tube of thousands of cloned animals, drifting and "
        "glowing."),

    # ── families and genera that carry a lot of this footage ──────────────────
    "Moridae": "Codlings and hakelings - deep-water relatives of the cods.",
    "Antimora": (
        "Blue hake: a slender, long-tailed deep-sea cod, common on continental "
        "slopes at a kilometre and below, and one of the most-filmed fish in this "
        "collection."),
    "Macrouridae": (
        "Grenadiers or rattails: a big head, a tapering tail, and the most "
        "frequently seen fish family on the deep-sea floor."),
    "Rhopalonematidae": "Small trachymedusan jellyfish of the open and deep water.",
    "Benthocodon": (
        "A dark red hydromedusa that hovers just off the seabed with hundreds of "
        "fine tentacles trailing. Red is black at depth - the colour is camouflage."),
    "Cladorhizidae": (
        "The carnivorous sponges: no filtering system left, just hooked spicules "
        "that catch small crustaceans, which the sponge then digests where they "
        "stuck."),
    "Asbestopluma": (
        "A carnivorous sponge shaped like a feather or a bottlebrush, standing on a "
        "stalk in the mud."),
    "Elpidiidae": (
        "Sea pigs: pink, inflated sea cucumbers walking the abyssal plain on enlarged "
        "tube feet, often in herds facing the same way."),
    "Synallactidae": "Large deposit-feeding sea cucumbers of the deep bottom.",
    "Psychropotidae": (
        "Deep-sea cucumbers with a sail on the back, thought to catch the current."),
    "Munidopsidae": (
        "Deep-water squat lobsters: not lobsters, and not crabs - a long-tailed "
        "relative that holds the tail tucked under."),
    "Coralliidae": (
        "The precious corals - a dense red or pink skeleton, slow-growing, and the "
        "reason deep-sea corals have been fished for centuries."),
    "Chrysogorgiidae": (
        "Golden corals: fine spiralling sea fans with a metallic sheen to the "
        "skeleton."),
    "Chrysogorgia": "A golden coral that grows as a single spiral whip or a spiral fan.",
    "Keratoisididae": (
        "Bamboo corals - jointed skeletons of alternating horn and chalk, banded "
        "like bamboo, and among the longest-lived colonial animals known."),
    "Primnoidae": "Sea fans armoured in overlapping plates, common on deep seamounts.",
    "Umbellulidae": (
        "Umbrella sea pens: a bare metre-long stalk in the mud with a single crown "
        "of polyps at the top."),
    "Funiculina": "A tall, whip-like sea pen, rooted in soft mud.",
    "Pennatulidae": "The feather-shaped sea pens.",
    "Paragorgia": "Bubblegum corals - thick, fleshy, pink to red sea fans, metres across.",
    "Metridium": "The plumose anemone: a column with a crown of fine white tentacles.",
    "Liponema": (
        "A deep-sea anemone that keeps almost no grip on the bottom and tumbles "
        "along it as a ball of tentacles."),
    "Heteropolypus": "A soft, fleshy deep-sea anemone-like octocoral of muddy bottoms.",
    "Poralia": (
        "A fragile deep red jellyfish that comes apart in the wash of a passing "
        "vehicle."),
    "Stygiomedusa": (
        "The giant phantom jelly: a metre-wide bell trailing four ribbon arms up to "
        "ten metres long. Seen a hundred or so times in a century of diving."),
    "Riftia": (
        "The giant tubeworm of Pacific hydrothermal vents: no mouth and no gut, fed "
        "entirely by sulphide-eating bacteria it houses in its own body."),
    "Poeobius": (
        "A polychaete that gave up looking like a worm - a gelatinous blob in the "
        "midwater that feeds on a mucus net."),
    "Vampyroteuthis": "The vampire squid.",
    "Pyrosoma": "A pyrosome colony - the drifting glowing tube.",
    "Chionoecetes": "Snow crabs and tanner crabs - long-legged crabs of cold bottoms.",
    "Paralomis": "A deep, spiny king-crab relative, slow-growing and long-lived.",
    "Chiroteuthis": (
        "A slender deep-sea squid with two very long tentacles held below it like "
        "fishing lines."),
    "Histioteuthis": (
        "The cockeyed squid: one small eye looking down and one huge eye looking up, "
        "each for a different kind of light."),
    "Opisthoteuthis": (
        "A flapjack octopus - flattened, finned, and usually sitting on the bottom."),
    "Strongylocentrotus": "The common green and red urchins of cold rocky bottoms.",
    "Psolus": "A sea cucumber armoured in scales, clamped to rock like a limpet.",
    "Pannychia": "A slender deep sea cucumber that glows blue when touched.",
    "Paelopatides": "A broad, flat, soft sea cucumber that can swim off the bottom.",
    "Merluccius": "Hakes - shoaling deep-water relatives of the cods.",
    "Sebastidae": "Rockfishes and thornyheads: spiny, long-lived fish of rock and slope.",
    "Zoarcidae": "Eelpouts - eel-shaped, sluggish fish of cold and deep bottoms.",
    "Liparidae": "Snailfish: soft, tadpole-shaped, and holding the depth record for fish.",
    "Psychrolutes": "Blobfish and their relatives - a fish built of jelly, not muscle.",
    "Bathysaurus": (
        "The deep-sea lizardfish: a mouthful of teeth on the bottom at three "
        "kilometres, and the deepest-living fish that hunts other fish."),
    "Embassichthys": "The deep-sea sole - a flatfish of the continental slope.",
    "Microstomus": "Dover and lemon soles.",
    "Laqueus": "A stalked lamp shell, attached to rock.",
    "Caecosagitta": "An arrow worm of deep water, with light organs on its fins.",
    "Torquaratoridae": (
        "Deep-sea acorn worms that graze the sediment and leave spiral trails "
        "behind them."),
    "Hastigerinella": (
        "A planktonic foraminiferan with a spiky glass shell, drifting in midwater."),
}


RANK_WORDS = {
    "kingdom": "kingdom", "phylum": "phylum", "class": "class", "order": "order",
    "family": "family", "genus": "genus", "species": "species",
    "subphylum": "subphylum", "subclass": "subclass", "suborder": "suborder",
    "infraorder": "infraorder", "superfamily": "superfamily",
    "subfamily": "subfamily", "tribe": "tribe", "subspecies": "subspecies",
}


def note(name: str) -> Optional[str]:
    """What this branch is, where somebody has written it down."""
    return NOTES.get(name)


def notes_for(names: List[str]) -> Dict[str, str]:
    """The notes for the names a page actually shows, and no others.

    The table covers every branch a reader can land on; a collection holds a few
    hundred of them. Shipping the whole table into every page would carry
    descriptions of animals this footage never saw.
    """
    return {name: NOTES[name] for name in sorted(set(names)) if name in NOTES}
