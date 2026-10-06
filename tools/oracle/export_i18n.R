#!/usr/bin/env Rscript
# Extraction des dictionnaires i18n depuis le package R SOURCE (clone 1.1.0) via l'API publique.
# Produit le JSON embarque cote Python (src/shinymanager/_data/labels.json), garantissant une
# parite exacte des labels (quirks et typos du source compris : voir zones de flou language).
#
# Usage : Rscript export_i18n.R <out.json>

suppressWarnings(suppressMessages(library(jsonlite)))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript export_i18n.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

reg <- use_language("en")$get_language_registered()
codes <- unname(reg)

labels <- list()
dt <- list()
date_input <- list()
for (code in codes) {
  lan <- use_language(code)
  labels[[code]] <- lan$get_all()
  d <- lan$get_DT()
  dt[[code]] <- if (is.null(d)) NULL else d
  date_input[[code]] <- lan$get_dateInput()
}

# ECART ASSUME (decision Jeremy, gate module 1) : le source stocke la traduction DataTables
# chinoise sous la cle "cn", jamais atteinte par get_DT("zh-CN") -> zh-CN n'a pas de DT en R.
# On corrige en mappant zh-CN sur cette traduction "cn" (lecture du champ prive R6 DT_lan).
dt_lan_internal <- use_language("en")$.__enclos_env__$private$DT_lan
if (!is.null(dt_lan_internal[["cn"]])) {
  dt[["zh-CN"]] <- dt_lan_internal[["cn"]]
}

payload <- list(
  version = as.character(packageVersion("shinymanager")),
  registered = as.list(stats::setNames(names(reg), unname(reg))),  # code -> nom affichage
  codes = codes,
  labels = labels,
  dt = dt,
  dateInput = date_input
)

writeLines(toJSON(payload, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("i18n exporte:", length(codes), "langues ->", out_file, "\n")
