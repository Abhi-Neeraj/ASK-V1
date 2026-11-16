"""Turning a speaker-labelled transcript into what happened in it, with fixed rules and no generative AI.

    transcript sentences
      -> `functions`: which activity each sentence is (decision, action, commitment, question, ...)
      -> `extract`: the task in it, who owns it, when it is due, and what it was said in reply to
      -> `analyze`: all of that for a whole recording, as plain data
      -> `summary`: a written summary built from that data by templates

Nothing here writes new sentences with a language model. Sentences are matched to activities by two things
added together: cue words and phrases (the rules), and how close the sentence is in meaning to example
sentences of each activity (a sentence embedding model when one is installed, otherwise a words-and-letters
fingerprint). The summary is assembled from the extracted pieces by templates, so the same transcript always
gives the same summary and every line of it can be traced to a sentence that was said.
"""
