#!/usr/bin/env python3

# bamdam by Bianca De Sanctis, bddesanctis@gmail.com 
# https://github.com/bdesanctis/bamdam/tree/main

import sys 
import re
import csv
import pysam
import math
import argparse
import os
import hyperloglog
import subprocess

def get_sorting_order(file_path):
    # a bam is almost a gzipped sam; take advantage of this so we don't have to read in the full header (pysam can't stream it)
    command = f"gunzip -dc {file_path}"
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)

    # read the output in binary and manually decode it as needed
    while True:
        line = process.stdout.readline()
        hd_index = line.find(b'@HD')
        if hd_index != -1:
            line = line[hd_index:]
            line = line.decode('ascii')
            fields = line.strip().split("\t")
            for field in fields:
                if field.startswith("SO:"):
                    sorting_order = field.split(":")[1]
                    process.stdout.close()
                    process.terminate()
                    return sorting_order
            break  # no need to continue if we've passed the @HD line

    process.stdout.close()
    process.terminate()
    return "unknown"

def write_shortened_lca(original_lca_path,short_lca_path,upto,mincount,exclude_keywords, exclude_under):

    print("\nWriting a filtered LCA file...")

    lcaheaderlines = 0
    with open(original_lca_path, 'r') as lcafile:
        for lcaline in lcafile:
            if "root" in lcaline:
                break
            lcaheaderlines += 1

    # pass 1: make a dictionary with all the tax ids and their counts 
    number_counts = {}
    with open(original_lca_path, 'r') as file:
        for _ in range(lcaheaderlines):
            next(file) 
        for line in file:
            if upto in line:
                if exclude_under and any(keyword in line for keyword in exclude_keywords): # checks all nodes in the path 
                    continue
                    # if you want to exclude all nodes underneath the ones you specified too, all you have to do is look for all the keywords in every line 
                else:
                    entry = line.strip().split('\t')
                    if len(entry) > 1:  
                        fields = entry[1:] # can ditch the read id 
                        if (not exclude_under) and any(keyword in fields[0] for keyword in exclude_keywords):
                            continue # this only checks the node the read is actually assigned to 
                        else:
                            keepgoing = True; field = 0
                            while keepgoing:
                                taxid = fields[field].split(':')[0]
                                if taxid in number_counts:
                                    number_counts[taxid] += 1
                                else:
                                    number_counts[taxid] = 1
                                if upto in fields[field]:
                                    keepgoing = False
                                field +=1 

    goodnodes = [key for key, count in number_counts.items() if count >= mincount]
    # these are the nodes that have at least the min count of reads assigned to them (or below them), and which are at most upto

    # pass 2: rewrite lines into a new lca file that pass the filter
    oldreadname = ""
    with open(original_lca_path, 'r') as infile, open(short_lca_path, 'w') as outfile:
            for _ in range(lcaheaderlines):
                next(infile) # assumes it has the usual 2 comment lines at the top from ngslca
            for line in infile:
                entry = line.strip().split('\t')
                readnamesplit = entry[0].split(':')[0:7]
                newreadname = ":".join(readnamesplit)
                if newreadname == oldreadname:
                    print("Error: You have duplicate entries in your LCA file, for example " + newreadname + ". That's a problem. You should fix this (e.g. using uniq) and re-run BamDam.")
                    exit -1
                if upto in line:
                    # you can just go straight to the upto level and check if that node has high enough count 
                    if exclude_under and any(keyword in line for keyword in exclude_keywords):
                        continue # don't keep the ones that have the keywords anywhere if exclude_under is true
                    elif (not exclude_under) and any(keyword in entry[1] for keyword in exclude_keywords):
                        continue # don't keep the ones that have the keywords in their assignment if exclude_under is false
                    else:
                        for field in entry[1:]:
                            if f":{upto}" in field:
                                number = field.split(':')[0]
                                if number in goodnodes:
                                    outfile.write(line)
                                    break

    print("Wrote a filtered lca file. \n")

def write_shortened_bam(original_bam_path,short_lca_path,short_bam_path,stranded,minsimilarity): 
    # runs through the existing bam and the new short lca file at once, and writes only lines to the new bam which are represented in the short lca file
    # does two passes, the first of which makes a shortened header as well and adds the command str to the end of the bam header
    # also annotates with (correct!) pmd scores as it goes

    # now takes in minsimilarity as a percentage, and will keep reads w/ equal to or greater than NM flag to this percentage 

    print("Writing a filtered BAM file annotated with PMD scores...")

    # go and get get header lines in the OUTPUT lca, not the input (it will be 0, but just in case I modify code in the future)
    lcaheaderlines = 0
    with open(short_lca_path, 'r') as lcafile:
        for lcaline in lcafile:
            if "root" in lcaline:
                break
            lcaheaderlines += 1
    command_str =  ' '.join(sys.argv) 

    # Pass one: get the shortened header
    # (Go through the short_lca_path and the original BAM header and the original BAM at the same time, batching in a list and putting in a dictionary)

    relevant_references = {}
    batch_references = []

    with pysam.AlignmentFile(original_bam_path, "rb", check_sq=False, require_index=False) as infile, \
         open(short_lca_path, 'r') as shortlcafile:
        
        for _ in range(lcaheaderlines):
            lcaline = next(shortlcafile)

        lcaline = next(shortlcafile)
        lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])

        notdone = True
        try:
            bamread = next(infile)
        except StopIteration:
            notdone = False

        while notdone:
            if bamread.query_name == lcareadname:
                while bamread.query_name == lcareadname:  # go through all the alignments for this read
                    similarity = 1 - bamread.get_tag('NM') / bamread.query_length
                    if similarity >= minsimilarity:
                        ref_name = infile.get_reference_name(bamread.reference_id)
                        batch_references.append(ref_name)  # Accumulate in the batch
                    try:
                        bamread = next(infile)
                    except StopIteration:
                        notdone = False
                        break
                try:
                    lcaline = next(shortlcafile)
                    lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])
                except StopIteration:
                    notdone = False  # done with the LCA file
                
                # process the batch and update the relevant_references dictionary
                for ref_name in set(batch_references):  # remove duplicates
                    relevant_references[ref_name] = True
                batch_references.clear()  #cClear the batch list for the next read
            else:
                try:
                    bamread = next(infile)
                except StopIteration:
                    notdone = False  # Done with the BAM file

        # filter the header to only include relevant references
        command = f"gunzip -dc {original_bam_path}"
        bam_process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)

        header_dict = {'HD': {}, 'SQ': [], 'PG': []}

        #read through the header line by line to produce a filtered header while hopefully consuming less RAM
        while True:
            bam_line = bam_process.stdout.readline()
            if not bam_line:
                break
            try:
                line = bam_line.decode('ascii').strip()
            except UnicodeDecodeError:
                hd_index = bam_line.find(b'@HD')
                if hd_index != -1:
                    bam_line = bam_line[hd_index:]
                    line = bam_line.decode('ascii').strip()
            fields = line.split("\t") 
            for field in fields:
                if not field.startswith('@'):
                    break
                if field.startswith('@HD'):
                    header_dict['HD'] = dict(field.split(':') for field in line.split('\t')[1:])
                if field.startswith('@SQ'):
                    sq_dict = dict(f.split(':') for f in line.split('\t')[1:])
                    if sq_dict['SN'] in relevant_references:
                        header_dict['SQ'].append(sq_dict)    
                if field.startswith('@PQ'):
                    header_dict['PG'].append(dict(f.split(':') for f in line.split('\t')[1:]))

        # add the 'bamdam'  entry to the header's PG section
        header_dict['PG'].append({'ID': 'bamdam', 'PN': 'bamdam', 'CL': command_str})

        bam_process.stdout.close()
        bam_process.terminate()

        filtered_header = {
            'HD': header_dict['HD'],
            'SQ': header_dict['SQ'],
            'PG': header_dict['PG']
        }

        # create the mapping from old reference IDs to new reference IDs
        name_to_old_id = {sq['SN']: i for i, sq in enumerate(header_dict['SQ'])}
        old_to_new_ref_id_map = {name_to_old_id[sq['SN']]: new_id for new_id, sq in enumerate(filtered_header['SQ'])}

    # pass two: write the reads, starting with the filtered header 
    with pysam.AlignmentFile(original_bam_path, "rb", check_sq=False, require_index=False) as infile, \
         pysam.AlignmentFile(short_bam_path, "wb", header=infile.header) as outfile, \
         open(short_lca_path, 'r') as shortlcafile:

        # Skip the LCA header lines
        for _ in range(lcaheaderlines): 
            lcaline = next(shortlcafile)

        # Read the first LCA line and extract the read name
        lcaline = next(shortlcafile)
        lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])
    
        currentlymatching = False
        notdone = True

        try:
            bamread = next(infile)
        except StopIteration:
            notdone = False

        while notdone:
            if bamread.query_name == lcareadname:
                # Copy this line and all the rest until you hit a nonmatching LCA line
                similarity = 1 - bamread.get_tag('NM') / bamread.query_length
                if similarity >= minsimilarity:
                    # Remap the reference ID
                    if bamread.reference_id in old_to_new_ref_id_map:
                        bamread.reference_id = old_to_new_ref_id_map[bamread.reference_id]
                    # Remap the next_reference_id for paired-end reads
                    if bamread.next_reference_id in old_to_new_ref_id_map:
                        bamread.next_reference_id = old_to_new_ref_id_map[bamread.next_reference_id]
                    pmd = get_pmd(bamread, stranded)
                    bamread.tags += [('DS','%.3f' % pmd)]
                    outfile.write(bamread) # write the read!
                currentlymatching = True
                while currentlymatching:
                    try:
                        bamread = next(infile)
                        if bamread.query_name == lcareadname:
                            similarity = 1 - bamread.get_tag('NM') / bamread.query_length
                            if similarity >= minsimilarity:
                                if bamread.reference_id in old_to_new_ref_id_map:
                                    bamread.reference_id = old_to_new_ref_id_map[bamread.reference_id]
                                # Remap the next_reference_id for paired-end reads
                                if bamread.next_reference_id in old_to_new_ref_id_map:
                                    bamread.next_reference_id = old_to_new_ref_id_map[bamread.next_reference_id]
                                pmd = get_pmd(bamread, stranded)
                                bamread.tags += [('DS','%.3f' % pmd)]
                                outfile.write(bamread) # write the read! 
                        else:
                            currentlymatching = False
                    except StopIteration:
                        notdone = False
                        break
                try:
                    lcaline = next(shortlcafile)
                    lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])
                except StopIteration:
                    notdone = False
            else:
                try:
                    bamread = next(infile)
                except StopIteration:
                    notdone = False

    # pbar_bam.close()
    print("Wrote a filtered bam file. \n")

def get_mismatches(seq, cigar, md):  
    # parses a read, cigar and md string to determine nucleotide mismatches and positions. 

    # does not output info on insertions/deletions but accounts for them.
    # thanks jonas oppenheimer who wrote half of this function :)

    # goes in two passes: once w/ cigar and once w/ md 
    '''
    reconstructs a nucleotide mismatch + position table given the query, cigar and md strings
    '''

    cig = re.findall(r'\d+\D', cigar)
    md_pattern = re.compile(r'\d+|\^[A-Za-z]+|[A-Za-z]')
    md_list = md_pattern.findall(md)

    ref_seq = ''
    read_seq = ''
    query_pos = 0 # indexes the ref reconstruction
    read_pos = 0 # indexes the read reconstruction (which is the input read but with potential added "-"s if the ref has insertions)
    for x in cig:
        cat = x[-1]
        if cat in ['H', 'P']: # doesn't consume reference or query
            continue

        bases = int(x[:-1])

        if cat == 'S': # soft clip
            read_seq += seq[read_pos:read_pos + bases] # include the bases in the reconstructed read 
            # you should probably edit this, we won't want to include soft clipped bases, i don't think 
            continue

        elif cat in ['M', '=', 'X']: # match
            ref_seq += seq[query_pos:query_pos + bases]
            read_seq += seq[read_pos:read_pos + bases]
            query_pos += bases
            read_pos += bases

        elif cat in ['D', 'N']: # 'D' : the reference has something there and the read doesn't, but pad them both 
            ref_seq += 'N' * bases
            read_seq += '-' * bases

        elif cat == 'I': # I: the read has something there and the reference doesn't, but pad them both 
            read_seq += seq[read_pos:read_pos + bases] # 'N' * bases
            ref_seq += '-' * bases
            query_pos += bases
            read_pos += bases

        else:
            sys.exit("Error: You've got some strange cigar strings here.")
    
    ref_pos = 0
    read_pos = 0
    mismatch_list = [] # of format [ref, read, pos in alignment]
    for x in md_list:
        if x.startswith('^'): # remember this can be arbitrarily long (a ^ and then characters)
            num_chars_after_hat = len(x) -1 
            for i in range(0,num_chars_after_hat):
                currchar = x[i+1]
                refhere = currchar
                readhere = "-" 
                ref_pos += 1 # but not the read pos
                # don't need to append to mismatch list 
        else: 
            if x.isdigit(): # can be multiple digits 
                # you're a number or a letter
                # if you're a number, you're matching. skip ahead. don't need to add to the mismatch list. 
                ref_pos += int(x)
                read_pos += int(x)
            else: # it will only be one character at a time 
                refhere = x
                readhere = read_seq[ref_pos]
                # update ref_seq
                char_list = list(ref_seq)
                char_list[ref_pos] = x
                ref_seq = "".join(char_list)
                # moving on
                mismatch_list.append([refhere,readhere,read_pos +1]) # in genetics we are 1-based (a->g at position 1 means the actual first position, whereas python is 0-based) 
                if refhere is None or readhere is None:
                    print("Warning: There appears to be an inconsistency with seq " + seq + " and cigar " + cigar + " and md " + md)
                    break
                ref_pos += 1
                read_pos += 1
                # if you're a letter, you're mismatching. the letter is given in the ref. you can find the letter in the read in the seq[ref_pos]. 

    return [mismatch_list, ref_seq, read_seq]

def mismatch_table(read,cigar,md,flagsum,phred):
    # wrapper for get_mismatches that also reverse complements if needed, and mirrors around the middle of the read 
    # so you shouldn't have to keep the length

    # also, a convenient place to calculate a pmd score without rewriting large pieces of code

    # "read" here is seq, a character vector

    readlength = len(read)

    # first parse the mismatches
    mms, refseq, readseq = get_mismatches(read,cigar,md)

    # some processing: first, figure out if it's forward or backwards
    # second field of a bam is the 0, 16, ... it's called a flag sum and it parses like this (see bowtie2 manual)
    bin_flagsum = bin(flagsum)[::-1]
    bit_position = 4 #  2^4 = 16 this flag means it's aligned in reverse
    backwards = len(bin_flagsum) > bit_position and bin_flagsum[bit_position] == '1' # backwards is a boolean 
    if backwards: # flip all the positions and reverse complement all the nucleotides. the read in the bam is reverse-complemented if aligned to the reverse strand. 
        complement = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}
        mmsc = []
        for entry in range(0,len(mms)):
            new_entry = [
                complement.get(mms[entry][0]), 
                complement.get(mms[entry][1]), 
                readlength - mms[entry][2] +1
            ]
            mmsc.append(new_entry)
        phred = phred[::-1]
    else:
        mmsc = mms
    # now everything, EXCEPT unmerged but retained reverse mate pairs of paired end reads (which i should still try to catch later; to do), should be 5' -> 3' 

    for entry in range(0,len(mmsc)):
        pos = mmsc[entry][2]
        if pos > readlength/2:
            mmsc[entry][2] = -(readlength - pos +1)

    return mmsc

def rev_complement(seq):
    complement = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C', 'N': 'N', '-': '-'}
    return ''.join(complement[base] for base in reversed(seq))

def get_rep_kmer(seq): # representative canonical kmer representation for counting, gets the lexicographical min of a kmer and its rev complement
    rep_kmer = min(seq,rev_complement(seq))
    return rep_kmer
 
def get_pmd(read, stranded):
    ### important!!! the original PMDtools implementation has a bug:
    # in lines 868 and 900 of the main python script, it multiplies the likelihood by the double-stranded models, and then there is an if statement that multiplies by the single-stranded models
    # resulting in totally incorrect pmd scores for single-stranded mode. the reimplementation here should be correct.
    
    # input is a pysam read object
    seq = read.query_sequence
    cigar = read.cigarstring
    md = read.get_tag('MD')
    rawphred = read.query_qualities
    flagsum = read.flag

    # set pmd score parameters . these are their original parameters, and i need to do some testing, but i think they are sensible enough in general. 
    P = 0.3
    C = 0.01
    pi = 0.001 

    # important note! in the PMDtools manuscript, they say "DZ=0" in the null model.
    # however, in the PMDtools code, Dz=0.001 in the null model.
    # here i am making the latter choice because i think it makes more biological sense.
    Dn = 0.001

    # find out if you're backwards
    bin_flagsum = bin(flagsum)[::-1]
    bit_position = 4 #  2^4 = 16 this flag means it's aligned in reverse
    backwards = len(bin_flagsum) > bit_position and bin_flagsum[bit_position] == '1' # backwards is a boolean 
    # do something if you are:
    mmsc, refseq, readseq = get_mismatches(seq, cigar, md)
    # adjust phred index if there are things happening in the read
    phred = [0 if base in ['-', 'N'] else rawphred.pop(0) for base in readseq]

    # run through both sequences to add up pmd likelihoods
    refseqlist = list(refseq)
    readseqlist = list(readseq)
    readlength = len(readseqlist)
    if backwards:
        refseqlist = rev_complement(refseqlist)
        readseqlist = rev_complement(readseqlist)
        phred = phred[::-1]
    actual_read_pos = 0 # position in the read / position in the ref (they are aligned now)
    pmd_lik = 1
    null_lik = 1
    pos = 0 # need a separate tracker to cope with indels. if there's a "-" in the read reconstruction because of an insertion in the ref, it should not count as a "position" in the read

    if stranded == "ss":
        for b in range(0,readlength):
            if readseqlist[b] == "-":
                continue # no pos +=1 
            # looking for c-> anything anywhere
            if refseqlist[b] == "C" and (readseqlist[b] == "T" or readseqlist[b] == "C"):
                # everything is relevant to 5 prime and 3 prime ends, get both distances
                epsilon = 1/3 * 10**(-phred[b] / 10)
                z = pos + 1 # pos 1 has distance 1, from pmd manuscript
                y = readlength - pos
                Dz = ((1-P)**(z-1))*P + C
                Dy = ((1-P)**(y-1))*P + C
                if readseqlist[b] == "T": # ss m
                    pmd_lik *= 1 - ((1-pi)*(1-epsilon)*(1-Dz)*(1-Dy) + (1-pi)*epsilon*Dz*(1-Dy) + (1-pi)*epsilon*Dy*(1-Dz) + pi*epsilon*(1-Dz)*(1-Dy))
                    null_lik *= 1 - ((1-pi)*(1-epsilon)*(1-Dn)*(1-Dn) + (1-pi)*epsilon*Dn*(1-Dn) + (1-pi)*epsilon*Dn*(1-Dn) + pi*epsilon*(1-Dn)*(1-Dn))                 
                if readseqlist[b] == "C": # ss m
                    pmd_lik *= (1-pi)*(1-epsilon)*(1-Dz)*(1-Dy) + (1-pi)*epsilon*Dz*(1-Dy) + (1-pi)*epsilon*Dy*(1-Dz) + pi*epsilon*(1-Dz)*(1-Dy)
                    null_lik *= (1-pi)*(1-epsilon)*(1-Dn)*(1-Dn) + (1-pi)*epsilon*Dn*(1-Dn) + (1-pi)*epsilon*Dn*(1-Dn) + pi*epsilon*(1-Dn)*(1-Dn)
                pos +=1 

    if stranded == "ds":
        for b in range(0,readlength):
            if readseqlist[b] == "-":
                continue # no pos +=1 
            if refseqlist[b] == "C" and (readseqlist[b] == "T" or readseqlist[b] == "C"):
                # get distance and stuff to 5 prime end
                epsilon = 1/3 * 10**(-phred[b] / 10)
                z = pos + 1   # 5 prime
                Dz = ((1-P)**(z-1))*P + C

                if readseqlist[b] == "T": # ds mm 
                    pmd_lik *=  1 - ((1-pi)*(1-epsilon)*(1-Dz) + (1-pi)*epsilon*Dz + pi*epsilon*(1-Dz)) 
                    null_lik *= 1 - ((1-pi)*(1-epsilon)*(1-Dn) + (1-pi)*epsilon*Dn + pi*epsilon*(1-Dn)) 
                
                if readseqlist[b] == "C": # ds match
                    pmd_lik *= (1-pi)*(1-epsilon)*(1-Dz) + (1-pi)*epsilon*Dz + pi*epsilon*(1-Dz)
                    null_lik *= (1-pi)*(1-epsilon)*(1-Dn) + (1-pi)*epsilon*Dn + pi*epsilon*(1-Dn)

            if refseqlist[b] == "G" and (readseqlist[b] == "A" or readseqlist[b] == "G"):
                # get distance and stuff to 3 prime end
                epsilon = 1/3 * 10**(-phred[b] / 10) # phred score 30 gives an error rate of 0.001 (then * 1/3)
                z = readlength - pos  # 3 prime
                Dz = ((1-P)**(z-1))*P + C
                if readseqlist[b] == "A": # ds mm
                    pmd_lik *=  1 - ((1-pi)*(1-epsilon)*(1-Dz) + (1-pi)*epsilon*Dz + pi*epsilon*(1-Dz)) 
                    null_lik *= 1 - ((1-pi)*(1-epsilon)*(1-Dn) + (1-pi)*epsilon*Dn + pi*epsilon*(1-Dn)) 
                if readseqlist[b] == "G": # ds m
                    pmd_lik *= (1-pi)*(1-epsilon)*(1-Dz) + (1-pi)*epsilon*Dz + pi*epsilon*(1-Dz)
                    null_lik *= (1-pi)*(1-epsilon)*(1-Dn) + (1-pi)*epsilon*Dn + pi*epsilon*(1-Dn)
            
            pos +=1 
    
    if pmd_lik == 0 or null_lik == 0:
        pmd_score = 0
    else:
        pmd_score = math.log(pmd_lik/null_lik)

    return pmd_score

# bunch of handy kmer functions
def generate_kmers_recursive(prefix, k, kmers):
    if k == 0:
        kmers.append(prefix)
        return
    for nucleotide in "ACGT":
        generate_kmers_recursive(prefix + nucleotide, k - 1, kmers)
def generate_kmers(k):
    kmers = []
    generate_kmers_recursive("", k, kmers)
    return kmers
def create_kmer_index(kmers):
    kmer_index = {kmer: index for index, kmer in enumerate(kmers)}
    return kmer_index
def generate_kmer_table(read, k):
    kmer_table = {}
    if k == len(read):
        kmer_table[read] = 1
    if k > len(read):
        print("Warning: Some of your reads are shorter than your specified k-mer length, which will lead to nonsensical behavior in the counting of unique k-mers. Consider reducing k and re-running.")
        kmer_table[read] = 1
    else: 
        for i in range(len(read) - k + 1):
            kmer = read[i:i + k]
            if not all(base in {'A', 'C', 'T', 'G'} for base in kmer):                
                continue # skip this k-mer, non ACTG characters are not allowed
            if kmer in kmer_table:
                kmer_table[kmer] += 1
            else:
                kmer_table[kmer] = 1
    return kmer_table
def map_kmers_to_index(kmer_table, kmer_index):
    mapped_kmers = {kmer_index[kmer]: count for kmer, count in kmer_table.items() if kmer in kmer_index}
    return mapped_kmers

def calculate_kmer_complexity(kmers, kr):
    # this takes in a dict of kmer counts, keyed by their indices, and returns an entropy
    num_possible_kmers = 4**kr
    entropy = 0
    for key in kmers:
        entropy += - kmers[key] / num_possible_kmers * math.log(kmers[key] / num_possible_kmers)
    
    pairwise_differences = 0
    keys = list(kmers.keys())
    sumkeys = 0
    for i in range(len(keys)):
        sumkeys += kmers[keys[i]]
        for j in range(i + 1, len(keys)):
            pairwise_differences += abs(kmers[keys[i]] - kmers[keys[j]])
    gini = pairwise_differences / (2*len(kmers)*sumkeys)
    
    return gini

def gather_subs_and_kmers(bamfile_path, lcafile_path, kr, kn, upto,stranded):
    # takes in a list of node ids as determined from shorten_files, and outputs the damage distribution at each of these nodes. 
    # Run through the bam/lca once and assign kmer complexity and damage to each node you care about. 
    print("\n Gathering substitution and kmer metrics per node...")

    # initialize kmer list so that indexing is fast and easy
    #kmers = generate_kmers(k)
    #kmer_index = create_kmer_index(kmers)

    lcaheaderlines = 0
    with open(lcafile_path, 'r') as lcafile:
        for lcaline in lcafile:
            if "root" in lcaline:
                break
            lcaheaderlines += 1
    
    # initialize 

    node_data = {}
    bamfile = pysam.AlignmentFile(bamfile_path, "rb") 
    lcafile = open(lcafile_path, 'r')
    oldreadname = ""
    nodestodumpinto = []
    num_alignments = 0
    currentsubdict = {}
    nms = 0 
    pmdsover2 = 0
    pmdsover4 = 0
    ctp1 = 0
    ctm1 = 0
    gam1 = 0
    div = 0
    stop = False # for debugging

    for _ in range(lcaheaderlines +1):
        currentlcaline = next(lcafile) 

    for read in bamfile:

        # get the basic info for this read
        readname = read.query_name

        # immediately find out if it's a new read. if so, you JUST finished the last read, so do a bunch of stuff for it.
        if readname != oldreadname and oldreadname != "":

            # go get the k-mer table now
            kmer_table = generate_kmer_table(seq,kr)
            #kmer_indices = map_kmers_to_index(kmer_table,kmer_index) # i think we don't need this 

            # get the lca entry and nodes we wanna update
            lcaentry = currentlcaline.split('\t')
            fields = lcaentry[1:]
            nodestodumpinto = []
            for i in range(len(fields)):
                if upto in fields[i]:
                    nodestodumpinto.append(fields[i].split(':')[0])
                    break
                nodestodumpinto.append(fields[i].split(':')[0])
            lcareadnamesplit = lcaentry[0].split(':')[0:7]
            lcareadname = ":".join(lcareadnamesplit)
            if oldreadname != lcareadname:
                print("WARNING: There is a mismatch between your lca and bam files at read " + oldreadname + " in the bam and " + lcareadname + " in the lca. ")
                print("This mismatch could have some bad downstream implications, and it would be best to find out what's causing it, fix it, and re-run. Perhaps you have some reads in one file but not in the other? \n")
                break

            # now update everything to all the relevant nodes
            for node in nodestodumpinto:
                if node not in node_data:
                    # that's ok! add it. 
                    node_data[node] = {'total_reads': 0,'pmdsover2': 0, 'pmdsover4': 0, 'meanlength': 0, 'total_alignments': 0, 
                                       'ani': 0, 'avgperreadgini' : 0, 'avggc': 0, 'tax_path' : "", 'subs': {},
                                       'dp1' : 0, 'dm1' : 0, 'div': 0, 'hll': hyperloglog.HyperLogLog(0.01), 'totalkmers' : 0 }
                node_data[node]['meanlength'] = ((node_data[node]['meanlength'] * node_data[node]['total_reads']) + readlength) / (node_data[node]['total_reads'] + 1)
                node_data[node]['avgperreadgini'] = ( (node_data[node]['avgperreadgini'] * node_data[node]['total_reads']) + calculate_kmer_complexity(kmer_table,kr)) / (node_data[node]['total_reads'] + 1)
                ani_for_this_read = (readlength - nms/num_alignments)/readlength 
                node_data[node]['ani'] = (ani_for_this_read + node_data[node]['ani'] * node_data[node]['total_reads']) / (node_data[node]['total_reads'] + 1)
                gc_content_for_this_read = (seq.count('C') + seq.count('G')) / readlength
                node_data[node]['avggc'] = ((node_data[node]['avggc'] * node_data[node]['total_reads']) + gc_content_for_this_read) / (node_data[node]['total_reads'] + 1)
                node_data[node]['total_alignments'] += num_alignments
                node_data[node]['pmdsover2'] += pmdsover2 / num_alignments
                node_data[node]['pmdsover4'] += pmdsover4 / num_alignments
                # only consider a transition snp "damage" if it's in every alignment of a read! 
                ctp1 = currentsubdict.get("['C', 'T', 1]", 0) # c -> t on the pos 1 
                ctm1 = currentsubdict.get("['C', 'T', -1]", 0) # c -> t on the pos 1 
                gam1 = currentsubdict.get("['G', 'A', -1]", 0) # c -> t on the pos 1 
                # ok, so on josh kapp's suggestion, i used to only add to dp1 and dm1 if all the alignments for that read had a c>t, which does make sense.
                # but then it doesn't match the damage plots and that bugs me to no end, so i went back to adding the proportion of reads which had a c>t.
                # an if ctp1 == num_alignments:  will do it josh's way 
                node_data[node]['dp1'] = ((node_data[node]['dp1'] * node_data[node]['total_reads']) + (ctp1/num_alignments) ) / (node_data[node]['total_reads'] + 1)
                if stranded == "ss":
                    node_data[node]['dm1'] = ((node_data[node]['dm1'] * node_data[node]['total_reads']) + (ctm1/num_alignments) )  / (node_data[node]['total_reads'] + 1)
                if stranded == "ds":
                    node_data[node]['dm1'] = ((node_data[node]['dm1'] * node_data[node]['total_reads']) + (gam1/num_alignments) ) / (node_data[node]['total_reads'] + 1)

                # updates kmer hll without using any outside kmer functions
                if len(seq) > kn:
                    for i in range(len(seq) - kn + 1):
                        kmer = seq[i:i + kn]
                        if not all(base in {'A', 'C', 'T', 'G'} for base in kmer):                
                            continue # skip this k-mer, non ACTG characters are not allowed
                        else:
                            node_data[node]['hll'].add(get_rep_kmer(kmer)) 
                            node_data[node]['totalkmers'] += 1 
                else:
                    print("Warning: One of your reads is shorter than kn. We will skip it for kmer computations and move on, but consider using a different kn if this message appears many times.")

                # updates substitution tables similarly
                other_sub_count = 0
                if currentsubdict:
                    for sub, count in currentsubdict.items():
                        if not ((sub[0] == 'C' and sub[1] == 'T') or (sub[0] == 'G' and sub[1] == 'A')):
                            other_sub_count += count # don't include c>t or g>a in any case, regardless of library 
                        if sub in node_data[node]['subs']: 
                            node_data[node]['subs'][sub] += count / num_alignments
                        else:
                            node_data[node]['subs'][sub] = count / num_alignments # so, this can be up to 1 per node. 
                div = other_sub_count / (num_alignments * readlength)
                node_data[node]['div'] = ((node_data[node]['div'] * node_data[node]['total_reads']) + div ) / (node_data[node]['total_reads'] + 1)
                # add the tax path if it's not already there
                if node_data[node]['tax_path'] == "":
                    lca_index = next(i for i, entry in enumerate(lcaentry) if entry.startswith(node))
                    tax_path = ','.join(lcaentry[lca_index:]).replace('\n','')
                    node_data[node]['tax_path'] = tax_path
                
                # only now update total reads 
                node_data[node]['total_reads'] += 1

            # move on. re initialize a bunch of things here 
            oldreadname = readname
            currentlcaline = next(lcafile)
            currentsubdict = {}
            num_alignments = 0
            nms = 0
            pmdsover2 = 0
            pmdsover4 = 0
            ctp1 = 0
            ctm1 = 0
            gam1 = 0
            div = 0 

        # now for the current read
        seq = read.query_sequence
        readlength = len(seq)
        cigar = read.cigarstring
        md = read.get_tag('MD')
        nms += read.get_tag('NM')
        pmd = float(read.get_tag('DS'))
        if(pmd>2):
            pmdsover2 += 1
        if(pmd>4):
            pmdsover4 += 1
        phred = read.query_qualities
        flagsum = read.flag
        num_alignments += 1 

        # get the mismatch table for this read, don't bother rerunning the internal pmd calculation though
        subs = mismatch_table(seq,cigar,md,flagsum,phred) 
        for sub in subs:
            key = "".join(str(sub))
            if key in currentsubdict:
                currentsubdict[key] +=1
            else:
                currentsubdict[key] = 1

        for key in subs:
            if key[2] == 0:
                print("Something is wrong. Printing the problem read. Check your reads and MD tags. ")
                print(f"Substitution: {key}, Read: {read.query_name}, md: {md}, cigar: {cigar}")

        if stop:
            break

        # quick catch for the starting read
        if oldreadname == "":
            oldreadname = readname

    print("Gathered substitution and kmer data for " + str(len(node_data)) + " taxonomic nodes. Now processing to compute damage and k-mer complexity... ")

    bamfile.close() 
    lcafile.close()

    return node_data

def format_subs(subs, nreads, stranded):
    formatted_subs = []
    other_subs = {}
    
    for key, value in subs.items():
        # Extract position and check if it is within the range -15 to 15
        parts = key.strip("[]").replace("'", "").split(", ")
        pos = int(parts[2])
        if -15 <= pos <= 15:
            substitution = parts[0] + parts[1]
            formatted_key = "".join(parts)
            formatted_value = round(value / nreads, 3)
            
            if substitution == 'CT': # keep any c>t
                formatted_subs.append((pos, f"{formatted_key}:{formatted_value}"))
            elif substitution == 'GA' and stranded == "ds": # keep g>a only if you are double stranded
                formatted_subs.append((pos, f"{formatted_key}:{formatted_value}"))
            else:
                if pos not in other_subs:
                    other_subs[pos] = 0.0
                other_subs[pos] += formatted_value
    
    # Add the summarized 'O' substitutions to the list
    for pos, value in other_subs.items():
        formatted_subs.append((pos, f"O{pos}:{round(value, 3)}"))
    
    # Sort the formatted_subs based on the specified order
    formatted_subs.sort(key=lambda x: (x[0] > 0, (x[0])))
    
    # Return the formatted substitutions as a string
    return " ".join(sub[1] for sub in formatted_subs)

def parse_and_write_node_data(nodedata, stats_path, subs_path, stranded):
    # parses a dictionary where keys are node tax ids, and entries are total_reads, meanlength, total_alignments, subs and kmers

    statsfile = open(stats_path, 'w', newline='')
    subsfile = open(subs_path, 'w', newline='')
    header = ['TaxNodeID', 'TaxName', 'TotalReads', 'ND+1', 'ND-1', 'UniqueKmers', 'Duplicity', 
              'MeanLength', 'Div', 'ANI','PerReadKmerGI', 'AvgGC', 'Damage+1', 'Damage-1','TotalAlignments', 
                'PMDsover2', 'PMDSover4','taxpath'] 
    statsfile.write('\t'.join(header) + '\n')
    writer = csv.writer(statsfile, delimiter='\t', quotechar='"', quoting=csv.QUOTE_NONNUMERIC)
    subswriter = csv.writer(subsfile, delimiter='\t', quotechar='"', quoting=csv.QUOTE_NONE)

    rows = []
    subsrows = {}

    for node in nodedata:
        tn = nodedata[node]
        
        # get formatted subs
        fsubs = format_subs(tn['subs'], tn['total_reads'], stranded)

        # number of unique k-mers approximated by the hyperloglog algorithm
        numuniquekmers = len(tn['hll'])
        duplicity = tn['totalkmers'] / numuniquekmers

        taxname = tn['tax_path'].split(",")[0].split(":")[1]

        # get normalized +1 and -1 damage frequencies
        dp1n = tn['dp1'] - tn['div']
        dm1n = tn['dm1'] - tn['div']

        # write 
        row = [int(node), taxname, tn['total_reads'],
               round(dp1n,4), round(dm1n,4), numuniquekmers, round(duplicity,2),
               round(tn['meanlength'], 2), round(tn['div'], 4), round(tn['ani'], 4), 
               round(tn['avgperreadgini'], 4), 
               round(tn['avggc'], 3), round(tn['dp1'],4), 
               round(tn['dm1'],4), 
               tn['total_alignments'], 
               round(tn['pmdsover2']/tn['total_reads'],3), round(tn['pmdsover4']/tn['total_reads'], 3),  
               tn['tax_path']] 
        rows.append(row)

        subsrows[int(node)] = [int(node), taxname, fsubs]
    
    # Sort rows by total reads
    rows.sort(key=lambda x: x[2], reverse=True)

    # Write sorted rows to stats file
    for row in rows:
        writer.writerow(row)
    
    # Write sorted subsrows based on the order of sorted rows 
    for row in rows:
        subswriter.writerow(subsrows[row[0]])

    statsfile.close()
    subsfile.close()

    print("Wrote final stats and subs files. Done!")

def extract_reads(in_lca, in_bam, out_bam, keyword):

    lcaheaderlines = 0
    with open(in_lca, 'r') as lcafile:
        for lcaline in lcafile:
            if "root" in lcaline:
                break
            lcaheaderlines += 1

    with pysam.AlignmentFile(in_bam, "rb", check_sq=False, require_index=False) as infile, \
         pysam.AlignmentFile(out_bam, "wb", header=infile.header) as outfile, \
         open(in_lca, 'r') as lcafile:

        # Skip the LCA header lines
        for _ in range(lcaheaderlines):
            lcaline = next(lcafile)

        # Read the first LCA line and extract the read name and check if it's got the keyword in it
        lcaline = next(lcafile)
        iskeywordinlca = keyword in lcaline
        lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])
        currentlymatching = False
        notdone = True

        # Get the first bam read
        try:
            bamread = next(infile)
        except StopIteration:
            sys.exit("Looks like your bam file is empty.")

        # iterate the lca file until you hit a keyword match. then, iterate the bam file until you hit the lca readname, and write all of those. 
        # then iterate the lca file again. 
        while notdone:
            if iskeywordinlca:
                # iterate the bam file until you find a match
                lcareadname = ":".join(lcaline.strip().split('\t')[0].split(':')[0:7])
                currentlymatching = bamread.query_name == lcareadname
                while not currentlymatching: # iterate until you hit the first instance of the read in the bam file
                    bamread = next(infile)
                    currentlymatching = bamread.query_name == lcareadname
                # now assume you are matching
                outfile.write(bamread)
                while currentlymatching:
                    try:
                        bamread = next(infile)
                        currentlymatching = bamread.query_name == lcareadname
                        if currentlymatching:
                            outfile.write(bamread)
                        else:
                            # done matching with this read name. increment the lca file
                            try:
                                lcaline = next(lcafile)
                                iskeywordinlca = keyword in lcaline
                            except StopIteration: 
                                notdone = False # done the lca file
                                break 
                    except StopIteration:
                        notdone = False
                        break # done the bam file
            else:
                while not iskeywordinlca:
                    try:
                        lcaline = next(lcafile)
                        iskeywordinlca = keyword in lcaline
                    except StopIteration:
                        notdone = False
                        break # done the lca file 

def plotter(in_bam, subs, tax, stranded, plotfile, r_script_path):

    if not os.path.exists(r_script_path):
        sys.stderr.write(f"Error: The R script '{r_script_path}' does not exist.\n")
        sys.exit(1)

    ds_tags = {}
    nm_tags = {}
    read_lengths = {}
    
    with pysam.AlignmentFile(in_bam, "rb") as bam:
        for read in bam:
            if read.has_tag("DS"):
                ds_tag = read.get_tag("DS")
                if ds_tag in ds_tags:
                    ds_tags[ds_tag] += 1
                else:
                    ds_tags[ds_tag] = 1
            if read.has_tag("NM"):
                nm_tag = read.get_tag("NM")
                if nm_tag in nm_tags:
                    nm_tags[nm_tag] += 1
                else:
                    nm_tags[nm_tag] = 1
            read_length = read.query_length
            if read_length in read_lengths:
                read_lengths[read_length] += 1
            else:
                read_lengths[read_length] = 1

        ds_stream = "\n".join(f"{ds}\t{count}" for ds, count in ds_tags.items())
        nm_stream = "\n".join(f"{nm}\t{count}" for nm, count in nm_tags.items())
        rl_stream = "\n".join(f"{length}\t{count}" for length, count in read_lengths.items())

        print(ds_tags)
        print(nm_tags)
        print(read_lengths)

        combined_stream = f"{ds_stream}\nEND_DS\n{nm_stream}\nEND_NM\n{rl_stream}\nEND_RL\n"
    command = [
        "Rscript", r_script_path,
        "-f", subs,
        "-t", tax,
        "-s", stranded,
        "-o", plotfile,
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True)
    process.communicate(combined_stream)


def shrink(args):
    write_shortened_lca(args.in_lca, args.out_lca, args.upto, args.mincount, args.exclude_keywords, args.exclude_under)
    write_shortened_bam(args.in_bam, args.out_lca, args.out_bam, args.stranded, args.minsim)

def compute(args):
    nodedata = gather_subs_and_kmers(args.in_bam, args.in_lca, kr=args.kr, kn=args.kn, upto=args.upto, stranded=args.stranded)
    parse_and_write_node_data(nodedata, args.out_stats, args.out_subs, args.stranded)  

def extract(args):
    extract_reads(args.in_lca, args.in_bam, args.out_bam, args.keyword)

def plot(args):
    plotter(args.in_bam, args.in_subs, args.tax, args.stranded, args.outplot, args.r_script_path)

def main():

    # Initialize
    parser = argparse.ArgumentParser(
        description="Bamdam processes LCA and bam files for ancient environmental DNA. Type bamdam command -h for more detailed help regarding a specific command.")
    
    subparsers = parser.add_subparsers(dest="command", required=True)

    script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    default_r_script_path = os.path.join(script_dir, "plotter.R")
    
    # Shrink
    parser_shrink = subparsers.add_parser('shrink', help="Filter the BAM and LCA files.")
    parser_shrink.add_argument("--in_lca", type=str, required=True, help="Path to the original (sorted) LCA file (required)")
    parser_shrink.add_argument("--in_bam", type=str, required=True, help="Path to the original (sorted) BAM file (required)")
    parser_shrink.add_argument("--out_lca", type=str, required=True, help="Path to the short output LCA file (required)")
    parser_shrink.add_argument("--out_bam", type=str, required=True, help="Path to the short output BAM file (required)")
    parser_shrink.add_argument("--stranded", type=str, required=True, help="Either ss for single stranded or ds for double stranded (required)")
    parser_shrink.add_argument("--mincount", type=int, default=5, help="Minimum read count to keep a node (default: 5)")
    parser_shrink.add_argument("--upto", type=str, default="family", help="Keep nodes up to and including this tax threshold, use root to disable (default: family)")
    parser_shrink.add_argument("--minsim", type=float, default=0.9, help="Minimum similarity to reference to keep an alignment (default: 0.9)")
    parser_shrink.add_argument("--exclude_keywords", type=str, nargs='+', default=[], help="Keyword(s) to exclude when filtering (default: none)")
    parser_shrink.add_argument("--exclude_keyword_file", type=str, default=None, help="File of keywords to exclude when filtering, one per line (default: none)")
    parser_shrink.add_argument("--exclude_under", action='store_true', help="Set this flag if you also want to exclude all nodes underneath the ones you've specified (default: not set)")
    parser_shrink.set_defaults(func=shrink)

    # Compute
    parser_compute = subparsers.add_parser('compute', help="Compute subs and stats files.")
    parser_compute.add_argument("--in_bam", type=str, required=True, help="Path to the BAM file (required)")
    parser_compute.add_argument("--in_lca", type=str, required=True, help="Path to the LCA file (required)")
    parser_compute.add_argument("--out_stats", type=str, required=True, help="Path to the output stats file (required)")
    parser_compute.add_argument("--out_subs", type=str, required=True, help="Path to the output subs file (required)")
    parser_compute.add_argument("--stranded", type=str, required=True, help="Either ss for single stranded or ds for double stranded (required)")
    parser_compute.add_argument("--kr", type=int, default=5, help="Value of k for per-read kmer complexity calculation (default: 5)")
    parser_compute.add_argument("--kn", type=int, default=29, help="Value of k for per-node counts of unique k-mers (default: 29)")
    parser_compute.add_argument("--upto", type=str, default="family", help="Keep nodes up to and including this tax threshold; use root to disable (default: family)")
    parser_compute.set_defaults(func=compute)

    # Extract
    parser_extract = subparsers.add_parser('extract', help="Extract alignments of reads containing a keyword in an associated lca file.")
    parser_extract.add_argument("--in_bam", type=str, required=True, help="Path to the BAM file (required)")
    parser_extract.add_argument("--in_lca", type=str, required=True, help="Path to the LCA file (required)")
    parser_extract.add_argument("--out_bam", type=str, required=True, help="Path to the filtered BAM file (required)")
    parser_extract.add_argument("--keyword", type=str, required=True, help="Keyword or phrase to filter for, e.g. a taxonomic node ID (required)")
    parser_extract.set_defaults(func=extract)

    # Plot
    parser_plot = subparsers.add_parser('plot', help="Bamdam plot is not done yet!!! Do not use this yet!")
                                        # Plot read length, edit distance, damage and PMD score distributions for a specified taxonomic node.")
    parser_plot.add_argument("--in_bam", type=str, required=True, help="Path to the BAM file containing only reads assigned to the specified node (required)")
    parser_plot.add_argument("--in_subs", type=str, required=True, help="Path to the subs file produced by bamdam compute (required)")
    parser_plot.add_argument("--tax", type=str, required=True, help="Taxonomic node ID (required)")
    parser_plot.add_argument("--stranded", type=str, required=True, help="Either ss for single stranded or ds for double stranded (required)")
    parser_plot.add_argument("--outplot", type=str, default="bamdam_plot.png", help="Filename for the output plot, ending in .png or .pdf")
    parser_plot.add_argument("--r_script_path", type=str, default=default_r_script_path, help="Path to the R script (default: ./plotter.R)")
    parser_plot.set_defaults(func=plot)

    args = parser.parse_args()

    if '--help' in sys.argv or '-h' in sys.argv:
        parser.print_help()
        sys.exit()
    
    # Validation checks
    if hasattr(args, 'stranded') and args.stranded not in ["ss", "ds"]:
        parser.error(f"Invalid value for stranded: {args.stranded}. Valid values are 'ss' or 'ds'.")
    if hasattr(args, 'mincount') and not isinstance(args.mincount, int):
        parser.error(f"Invalid integer value for mincount: {args.mincount}")
    if hasattr(args, 'kr') and (not isinstance(args.kr, int) or not isinstance(args.kr, int) or args.kr > 30):
        parser.error(f"Invalid integer value for kr : {args.kr} (max 29, and that is much higher than recommended in any case)")
    if hasattr(args, 'kn') and (not isinstance(args.kn, int) or not isinstance(args.kn, int) or args.kn > 50):
        parser.error(f"Invalid integer value for kn : {args.kn} (max 49, and that is much higher than recommended in any case)")
    if hasattr(args, 'upto') and not re.match("^[a-z]+$", args.upto):
        parser.error(f"Invalid value for upto: {args.upto}. Must be a string of only lowercase letters.")
    if hasattr(args, 'minsim') and not isinstance(args.minsim, float):
        parser.error(f"Invalid float value for minsim: {args.minsim}")
    if hasattr(args, 'in_lca') and not os.path.exists(args.in_lca):
        parser.error(f"Input LCA path does not exist: {args.in_lca}")
    if hasattr(args, 'in_bam') and not os.path.exists(args.in_bam):
        parser.error(f"Input BAM path does not exist: {args.in_bam}")
    if hasattr(args, 'upto') and args.upto=="clade":
        parser.error(f"Sorry, clade is not a valid taxonomic level in bamdam because there can be multiple clades in one taxonomic path.")
    if hasattr(args, 'upto') and args.upto != args.upto.lower():
        parser.warning(f"Warning: {args.upto} as provided is not in lowercase. Converting to lowercase and moving on.")
        args.upto = args.upto.lower()
        
    sortorder = get_sorting_order(args.in_bam)
    if sortorder != "queryname":
        print("Error: Your bam file does not appear to be read-sorted. Please try again with it once it has been read-sorted (samtools sort -n), which should be the same order as your lca file.")

    # Deal with exclude keywords
    if hasattr(args, 'exclude_keywords') or hasattr(args, 'exclude_keyword_file'):
        if args.exclude_keywords and args.exclude_keyword_file:
            parser.error("Please only provide one of --exclude_keywords or --exclude_keyword_file, not both.")
        # Initialize exclude_keywords
        exclude_keywords = args.exclude_keywords
        # Make it a list if it's not already
        if type(exclude_keywords) != type([]):
            exclude_keywords = [exclude_keywords]
        # Read exclude keywords from file if provided
        if args.exclude_keyword_file:
            if not os.path.exists(args.exclude_keyword_file):
                parser.error(f"Exclude keyword file path does not exist: {args.exclude_keyword_file}")
            with open(args.exclude_keyword_file, 'r') as f: # remove quotation marks if they're in the file 
                exclude_keywords.extend([line.strip().strip('"').strip("'") for line in f if line.strip()])

    
    if args.command == 'shrink':
        print("Hello! You are running bamdam shrink with the following arguments:")
        print(f"in_lca: {args.in_lca}")
        print(f"in_bam: {args.in_bam}")
        print(f"out_lca: {args.out_lca}")
        print(f"out_bam: {args.out_bam}")
        print(f"stranded: {args.stranded}")
        print(f"mincount: {args.mincount}")
        print(f"upto: {args.upto}")
        print(f"minsim: {args.minsim}")
        if hasattr(args, 'exclude_keyword_file') and args.exclude_keyword_file:
            print(f"exclude_keywords: loaded from {args.exclude_keyword_file}")
        if hasattr(args, 'exclude_keywords') and args.exclude_keywords:
            print(f"exclude_keywords: {args.exclude_keywords}")
        if hasattr(args, 'exclude_keyword_file') or hasattr(args, 'exclude_keywords'):
            print(f"exclude_under: {args.exclude_under}")

    elif args.command == 'compute':
        print("Hello! You are running bamdam compute with the following arguments:")
        print(f"in_bam: {args.in_bam}")
        print(f"in_lca: {args.in_lca}")
        print(f"out_stats: {args.out_stats}")
        print(f"out_subs: {args.out_subs}")
        print(f"stranded: {args.stranded}")
        print(f"kr: {args.kr}")
        print(f"kn: {args.kn}")
        print(f"upto: {args.upto}")

    elif args.command == 'extract':
        print("Hello! You are running bamdam extract with the following arguments:")
        print(f"in_bam: {args.in_bam}")
        print(f"in_lca: {args.in_lca}")
        print(f"out_bam: {args.out_bam}")
        print(f"keyword: {args.keyword}")

    elif args.command == 'plot':
        print("Hello! Plot is not functional yet!!! ") # You are running bamdam plot with the following arguments:")
        print(f"in_bam: {args.in_bam}")
        print(f"in_subs: {args.in_subs}")
        print(f"tax: {args.tax}")
        print(f"stranded: {args.stranded}")
        print(f"outplot: {args.outplot}")

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()


###### Minor comments

# Sometimes ngslca spits out duplicate lines. I don't know why. If it's causing problems, get rid of them before running! Like this
# awk '!seen[$0]++' input_lca > output_lca

# Ideally use the full tax node information when passing keywords.
# Specifying just 'Pedicularis' will also remove all reads mapping to the node 669767:Pedicularis hirsuta:species,
# even if you meant to exclude only the genus level node 43174:Pedicularis:genus and set exclude_under = False.
# On the other hand, specifying 43174:Pedicularis:genus or 43174:Pedicularis will act predictably.

