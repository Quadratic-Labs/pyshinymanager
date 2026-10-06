#!/usr/bin/env Rscript
# Runner oracle : execute une fonction du package R SOURCE (le clone 1.1.0, PAS la lib
# installee) sur des cas JSON, et ecrit les resultats en JSON pour comparaison cote Python.
#
# Usage : Rscript run_r.R <cases.json> <out.json>
#
# Le clone est charge via pkgload::load_all() pour garantir que l'oracle valide la version
# exacte que l'on migre (le systeme a shinymanager 1.0.410 installe, la source est 1.1.0).
#
# Format des cas (cases.json) : liste d'objets
#   { "id": "cas-01", "fn": "generate_pwd", "args": { ... } }
# Format de sortie (out.json) : liste d'objets
#   { "id": "cas-01", "ok": true, "value": <resultat>, "error": null }

suppressWarnings(suppressMessages({
  library(jsonlite)
}))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("Usage: Rscript run_r.R <cases.json> <out.json>")
cases_file <- args[[1]]
out_file <- args[[2]]

# Racine du clone source, relative a ce script : target/tools/oracle -> ../../../source/shinymanager
this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = FALSE)

if (requireNamespace("pkgload", quietly = TRUE) && dir.exists(src)) {
  suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))
  loaded_from <- src
} else {
  # Repli : lib installee (version potentiellement != source ; signale dans la sortie).
  suppressWarnings(suppressMessages(library(shinymanager)))
  loaded_from <- paste0("installed:", as.character(packageVersion("shinymanager")))
}

`%||%` <- function(a, b) if (is.null(a)) b else a

cases <- fromJSON(cases_file, simplifyDataFrame = FALSE)
results <- lapply(cases, function(case) {
  fn <- tryCatch(get(case$fn, mode = "function"), error = function(e) NULL)
  if (is.null(fn)) {
    return(list(id = case$id, ok = FALSE, value = NULL,
                error = paste0("fonction introuvable: ", case$fn)))
  }
  out <- tryCatch(
    list(id = case$id, ok = TRUE, value = do.call(fn, case$args %||% list()), error = NULL),
    error = function(e) list(id = case$id, ok = FALSE, value = NULL, error = conditionMessage(e))
  )
  out
})

writeLines(toJSON(list(loaded_from = loaded_from, results = results),
                  digits = NA, null = "null", na = "null", auto_unbox = TRUE, pretty = TRUE),
           out_file)
