#!/usr/bin/env Rscript
# Oracle pour le module passwords :
#  - VECTEURS_PARTAGES : hache une liste de mots de passe cote R (scrypt::hashPassword) que
#    Python devra verifier ; et verifie cote R des hash fournis par Python (compat bidirectionnelle).
#  - DIFF_FONCTIONNEL : validate_pwd sur un jeu de mots de passe.
#
# Usage : Rscript oracle_passwords.R <out.json> [python_hashes.json]
#   Si python_hashes.json est fourni (liste {pwd, hash}), R verifie chaque hash Python.

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_passwords.R <out.json> [python_hashes.json]")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

pwds <- c("azerty", "12345", "Abc123", "P@ssw0rd", "e")

# R hache -> pour verification cote Python
r_hashes <- lapply(pwds, function(p) list(pwd = p, hash = scrypt::hashPassword(p)))

# validate_pwd cote R sur un jeu de cas
vpwds <- c("Abc123", "abc123", "ABC123", "Abcdef", "Ab1", "Abcde1", "aB3xyz")
r_validate <- lapply(vpwds, function(p) list(pwd = p, valid = validate_pwd(p)))

# Verification cote R des hash produits par Python (si fournis)
py_verify <- NULL
if (length(args) >= 2 && file.exists(args[[2]])) {
  py <- fromJSON(args[[2]], simplifyDataFrame = FALSE)
  py_verify <- lapply(py, function(item) {
    list(pwd = item$pwd,
         verify_ok = scrypt::verifyPassword(item$hash, item$pwd),
         verify_ko = scrypt::verifyPassword(item$hash, paste0(item$pwd, "X")))
  })
}

writeLines(toJSON(list(r_hashes = r_hashes, r_validate = r_validate, py_verify = py_verify),
                  auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle passwords ecrit ->", out_file, "\n")
