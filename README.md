ClaSS2Style
===========


Converts CSS classes to inline styles
--------------------------------------

A simplified, but much more efficient version of [premailer](https://github.com/peterbe/premailer)
and [pynliner](https://github.com/rennat/pynliner).

Will only convert simple class definitions from your stylesheet, such as `.foo`,
however, it will do so in a fraction of the time that premailer or pynliner take,
so this is really targeted at large, almost semi-structured documents. In the original
case, this was applied to the output of [pdf2htmlEX](https://github.com/coolwanglu/pdf2htmlEX),
a popular PDF-to-HTML converter.


Getting started
---------------

First of all, change into the module's directory and run:

        $ python setup.py install

Next, the most basic use is to use the shortcut function, like this:

        >>> from ClaSS2Style import ClaSS2Style
        >>> print ClaSS2Style(some_html_data).transform()

The `ClaSS2Style` take a bunch of keyword arguments to control its behaviour,
check out the code of the `ClaSS2Style` class for the full list.
