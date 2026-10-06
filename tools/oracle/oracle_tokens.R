#!/usr/bin/env Rscript
# Oracle differentiel pour le store de tokens : execute des sequences deterministes sur une
# instance fraiche du R6 `.tokens` du package SOURCE (clone 1.1.0) et ecrit les sorties
# observables en JSON. Les tokens sont des chaines litterales (pas `generate`) pour rester
# deterministe. Le comportement dependant du temps (timeout) est teste cote Python uniquement.
#
# Usage : Rscript oracle_tokens.R <out.json>

suppressWarnings(suppressMessages(library(jsonlite)))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_tokens.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

tok <- .tokens$new()

res <- list()

# S1 : anti-rejeu is_valid (usage unique) puis reset_count
tok$add("t1", list(user = "alice", admin = "TRUE"))
res$s1_is_valid_seq <- c(tok$is_valid("t1"), tok$is_valid("t1"))
tok$reset_count("t1")
res$s1_after_reset <- c(tok$is_valid("t1"), tok$is_valid("t1"))

# S2 : is_valid_server stable et sans effet sur le compteur
res$s2_server_seq <- c(tok$is_valid_server("t1"), tok$is_valid_server("t1"), tok$is_valid_server("t1"))

# S3 : is_admin, coercition as.logical sur diverses valeurs
tok$add("adm_true", list(user = "a", admin = "TRUE"))
tok$add("adm_false", list(user = "b", admin = "FALSE"))
tok$add("adm_t", list(user = "c", admin = "T"))
tok$add("adm_yes", list(user = "d", admin = "yes"))
tok$add("adm_one", list(user = "e", admin = "1"))
tok$add("adm_missing", list(user = "f"))
res$s3_is_admin <- list(
  adm_true = tok$is_admin("adm_true"),
  adm_false = tok$is_admin("adm_false"),
  adm_t = tok$is_admin("adm_t"),
  adm_yes = tok$is_admin("adm_yes"),
  adm_one = tok$is_admin("adm_one"),
  adm_missing = tok$is_admin("adm_missing"),
  adm_unknown = tok$is_admin("jamais-vu")
)

# S4 : get exclut shinymanager_datetime ; get_user
res$s4_get_keys <- sort(names(tok$get("t1")))
res$s4_get_user <- tok$get_user("t1")

# S5 : is_valid sur token inconnu
res$s5_unknown <- tok$is_valid("jamais-vu")

# S6 : remove ne purge que les tokens valides (ZF-2) ; get renvoie encore les infos
tok$remove("t1")
res$s6_valid_server_after_remove <- tok$is_valid_server("t1")
res$s6_get_user_after_remove <- tok$get_user("t1")

# S7 : timeout <= 0 -> toujours valide
tok$set_timeout(0)
res$s7_timeout_zero <- tok$is_valid_timeout("adm_true")

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle tokens ecrit ->", out_file, "\n")
