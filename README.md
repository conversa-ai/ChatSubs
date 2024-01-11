# ChatSubs

This repository contains the code for building the ChatSubs corpus.

## Raw data

The raw subtitle data can be downloaded from [OpenSubtitles.org](https://dl.opensubtitles.org/addons/export/).

## Building the corpus

When the raw subtitle data is unpacked, the input folder has to point to the root folder where `export.txt` is stored. 

[The language codes](https://www.loc.gov/standards/iso639-2/php/code_list.php) are the same as the ones used by OpenSubtitles.org. The script is adapted for processing Spanish (spa), Catalan (cat), Basque (baq) and Galician (glg).

```
python extractDialogOpenSubtitles.py --inputfolder input_folder --outputfolder output_folder
```

## References

This dataset and publication is a result of the project CONVERSA (TED2021-132470B-I00) funded by MCIN/AEI /10.13039/501100011033 and by "European Union NextGenerationEU/PRTR".

To cite the dataset we kindly ask you to use the following reference.

```
@article{ConversaChatSubs,
      title = {ChatSubs: A dataset of dialogues in Spanish, Catalan, Basque and Galician extracted from movie subtitles for developing advanced conversational models},
      journal = {Data in Brief},
      volume = {50},
      pages = {109565},
      year = {2023},
      issn = {2352-3409},
      doi = {https://doi.org/10.1016/j.dib.2023.109565},
      url = {https://www.sciencedirect.com/science/article/pii/S2352340923006650},
      author = {Ksenia Kharitonova and Zoraida Callejas and David Pérez-Fernández and Asier Gutiérrez-Fandiño and David Griol},
      keywords = {Dialogue, Conversation, Chatbots, Conversational AI, Speech, Natural language processing},
}
```
